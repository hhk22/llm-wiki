"""Compare retrieval with/without version scoping, reusing each query embedding.

Run: uv run python scripts/evaluate_filters.py
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from evaluate import candidate_details, print_report

from llm_wiki.database import DatabaseSettings, connect_database
from llm_wiki.embedding import EmbeddingSettings, GeminiEmbeddingProvider
from llm_wiki.evaluation import evaluate_questions, load_evaluation_questions
from llm_wiki.search import (
    HYBRID_CANDIDATE_LIMIT,
    RRF_K,
    embed_query_with_retry,
    fuse_rrf,
    keyword_search,
    vector_search,
)
from llm_wiki.search_scope import SearchScope, infer_search_scope

ROOT = Path(__file__).resolve().parents[1]
METHODS = ("keyword", "vector", "hybrid", "keyword_filtered", "vector_filtered", "hybrid_filtered")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=ROOT / "evaluation/questions.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation/results-v2-filters.json")
    args = parser.parse_args()
    if args.output.resolve() in {
        (ROOT / "evaluation" / name).resolve()
        for name in ("results-v1.json", "results-v2-hybrid.json")
    }:
        parser.error("Choose a new output path; previous experiments must be preserved.")
    load_dotenv()
    questions = load_evaluation_questions(args.questions)
    settings = EmbeddingSettings.from_env()
    provider = GeminiEmbeddingProvider(settings)
    rankings = {method: {} for method in METHODS}
    diagnostics = []
    with connect_database(DatabaseSettings.from_env()) as connection:
        for question in questions:
            query = question.query
            scope = infer_search_scope(query)
            timings = {}
            start = time.perf_counter()
            embedding = embed_query_with_retry(provider, query)
            timings["query_embedding"] = time.perf_counter() - start
            candidates = {}
            for suffix, selected_scope in (("", SearchScope()), ("_filtered", scope)):
                for method in ("keyword", "vector"):
                    start = time.perf_counter()
                    if method == "keyword":
                        results = keyword_search(
                            connection, query, limit=HYBRID_CANDIDATE_LIMIT, scope=selected_scope
                        )
                    else:
                        results = vector_search(
                            connection,
                            embedding,
                            limit=HYBRID_CANDIDATE_LIMIT,
                            scope=selected_scope,
                        )
                    candidates[method + suffix] = results
                    timings[method + suffix] = time.perf_counter() - start
                start = time.perf_counter()
                candidates["hybrid" + suffix] = fuse_rrf(
                    candidates["keyword" + suffix],
                    candidates["vector" + suffix],
                    limit=HYBRID_CANDIDATE_LIMIT,
                )
                timings["hybrid" + suffix + "_fusion"] = time.perf_counter() - start
            for method in METHODS:
                rankings[method][query] = candidates[method]
            diagnostics.append(
                {
                    "question_id": question.question_id,
                    "query": query,
                    "scope": asdict(scope),
                    "timings_seconds": timings,
                    "candidates": {
                        method: candidate_details(results) for method, results in candidates.items()
                    },
                }
            )
            print(f"evaluated={question.question_id} scope={scope.mode}", flush=True)
    reports = [
        evaluate_questions(
            questions,
            lambda query, top_k, m=method: rankings[m][query][:top_k],
            method=method,
        )
        for method in METHODS
    ]
    comparisons = {}
    for base, filtered in zip(reports[:3], reports[3:], strict=True):
        previous = {r.question_id: r for r in base.results}
        comparisons[filtered.method] = {
            metric: {
                "improved": [
                    r.question_id
                    for r in filtered.results
                    if getattr(r, metric) is True
                    and getattr(previous[r.question_id], metric) is False
                ],
                "regressed": [
                    r.question_id
                    for r in filtered.results
                    if getattr(r, metric) is False
                    and getattr(previous[r.question_id], metric) is True
                ],
            }
            for metric in ("hit_at_1", "hit_at_3")
        }
    payload = {
        "question_set": str(
            args.questions.relative_to(ROOT)
            if args.questions.is_relative_to(ROOT)
            else args.questions
        ),
        "top_k": 3,
        "candidate_limit_per_method": HYBRID_CANDIDATE_LIMIT,
        "rrf_k": RRF_K,
        "rrf_weights": {"keyword": 1, "vector": 1},
        "filters": "query intent -> numeric version of deploy-guide, before ranking",
        "reranker": False,
        "embedding_model": settings.model,
        "embedding_dimensions": settings.dimensions,
        "embedding_operations": len(questions),
        "measurement_note": "One pass, not p50/p95. One query embedding reused across variants; "
        "HTTP retries may add requests. Document Hit does not prove answer "
        "evidence is present in the representative chunk. No generation.",
        "reports": [r.to_dict() for r in reports],
        "compared_to_unfiltered": comparisons,
        "diagnostics": diagnostics,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    for report in reports:
        print_report(report)
    print(f"comparisons={json.dumps(comparisons, ensure_ascii=False)}")
    print(f"results={args.output}")


if __name__ == "__main__":
    main()
