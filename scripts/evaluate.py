"""Compare keyword normalization and RRF hybrid retrieval on the fixed question set.

Run: uv run python scripts/evaluate.py
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from llm_wiki.database import DatabaseSettings, connect_database
from llm_wiki.embedding import EmbeddingSettings, GeminiEmbeddingProvider
from llm_wiki.evaluation import EvaluationReport, evaluate_questions, load_evaluation_questions
from llm_wiki.search import (
    HYBRID_CANDIDATE_LIMIT,
    RRF_K,
    SearchResult,
    embed_query_with_retry,
    fuse_rrf,
    keyword_search,
    normalize_keyword_query,
    vector_search,
)
from llm_wiki.search_scope import SearchScope

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = ROOT / "evaluation" / "questions.yaml"
RESULTS_PATH = ROOT / "evaluation" / "results-v2-hybrid.json"
METHODS = ("keyword_raw", "keyword", "vector", "hybrid_raw", "hybrid")


def candidate_details(results: list[SearchResult]) -> list[dict[str, Any]]:
    return [
        {
            "document_id": result.document_id,
            "chunk_index": result.chunk_index,
            "heading_path": result.heading_path,
            "content": result.content,
            "score": result.score,
        }
        for result in results
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=RESULTS_PATH)
    args = parser.parse_args()
    if args.output.resolve() == (ROOT / "evaluation" / "results-v1.json").resolve():
        parser.error("The v1 baseline must not be overwritten. Choose a different --output.")

    load_dotenv()
    questions = load_evaluation_questions(QUESTIONS_PATH)
    settings = EmbeddingSettings.from_env()
    provider = GeminiEmbeddingProvider(settings)
    rankings: dict[str, dict[str, list[SearchResult]]] = {method: {} for method in METHODS}
    diagnostics = []

    with connect_database(DatabaseSettings.from_env()) as connection:
        for question in questions:
            query = question.query
            timings = {}
            candidates = {}
            for method, normalize in (("keyword_raw", False), ("keyword", True)):
                start = time.perf_counter()
                candidates[method] = keyword_search(
                    connection, query, limit=HYBRID_CANDIDATE_LIMIT, normalize=normalize,
                    scope=SearchScope(),
                )
                timings[method] = time.perf_counter() - start

            start = time.perf_counter()
            query_vector = embed_query_with_retry(provider, query)
            timings["query_embedding"] = time.perf_counter() - start
            start = time.perf_counter()
            candidates["vector"] = vector_search(
                connection, query_vector, limit=HYBRID_CANDIDATE_LIMIT
            )
            timings["vector"] = time.perf_counter() - start

            for method, keyword_method in (("hybrid_raw", "keyword_raw"), ("hybrid", "keyword")):
                start = time.perf_counter()
                candidates[method] = fuse_rrf(
                    candidates[keyword_method], candidates["vector"], limit=HYBRID_CANDIDATE_LIMIT
                )
                timings[method + "_fusion"] = time.perf_counter() - start
            for method in METHODS:
                rankings[method][query] = candidates[method]

            diagnostics.append(
                {
                    "question_id": question.question_id,
                    "query": query,
                    "normalized_keyword_query": normalize_keyword_query(query),
                    "timings_seconds": timings,
                    "candidates": {
                        method: candidate_details(results) for method, results in candidates.items()
                    },
                }
            )
            print(f"evaluated={question.question_id}", flush=True)

    reports = [
        evaluate_questions(
            questions,
            lambda query, top_k, method=method: rankings[method][query][:top_k],
            method=method,
        )
        for method in METHODS
    ]
    vector_results = {result.question_id: result for result in reports[2].results}
    comparisons = {}
    for report in reports[3:]:
        comparisons[report.method] = {
            metric: {
                "improved": [
                    result.question_id for result in report.results
                    if getattr(result, metric) is True
                    and getattr(vector_results[result.question_id], metric) is False
                ],
                "regressed": [
                    result.question_id for result in report.results
                    if getattr(result, metric) is False
                    and getattr(vector_results[result.question_id], metric) is True
                ],
            }
            for metric in ("hit_at_1", "hit_at_3")
        }

    payload = {
        "question_set": QUESTIONS_PATH.relative_to(ROOT).as_posix(),
        "top_k": 3,
        "candidate_limit_per_method": HYBRID_CANDIDATE_LIMIT,
        "rrf_k": RRF_K,
        "rrf_weights": {"keyword": 1, "vector": 1},
        "tie_break": "document_id ASC",
        "representative_chunk": "vector when available, otherwise keyword",
        "filters": [],
        "reranker": False,
        "embedding_model": settings.model,
        "embedding_dimensions": settings.dimensions,
        "embedding_requests": len(questions),
        "measurement_note": "One pass; timings include warm-up effects. Not p50/p95. "
        "All variants reuse each question's vector candidates and one embedding operation; "
        "transient provider retries may increase HTTP request count. No answer generation.",
        "reports": [report.to_dict() for report in reports],
        "compared_to_vector": comparisons,
        "diagnostics": diagnostics,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for report in reports:
        print_report(report)
    print(f"compared_to_vector={json.dumps(comparisons, ensure_ascii=False)}")
    print(f"results={args.output}")


def print_report(report: EvaluationReport) -> None:
    print(f"\nmethod={report.method}")
    print(
        f"Hit@1={report.hit_at_1_count}/{report.single_document_questions} ({report.hit_at_1:.1%})"
    )
    print(f"Hit@3={report.hit_at_3_count}/{report.questions} ({report.hit_at_3:.1%})")
    for metrics in report.by_type:
        hit_at_1 = "-" if metrics.hit_at_1 is None else f"{metrics.hit_at_1:.1%}"
        print(f"type={metrics.question_type} Hit@1={hit_at_1} Hit@3={metrics.hit_at_3:.1%}")


if __name__ == "__main__":
    main()
