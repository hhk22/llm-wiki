"""Compare one/two source chunks per selected document, with sampled grounded answers.

Run: uv run python scripts/evaluate_evidence.py
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from evaluate import print_report

from llm_wiki.answering import (
    GeminiAnswerProvider,
    GenerationRequestError,
    GenerationSettings,
    answer_with_retry,
    build_grounded_prompt,
)
from llm_wiki.database import DatabaseSettings, connect_database
from llm_wiki.embedding import EmbeddingSettings, GeminiEmbeddingProvider
from llm_wiki.evaluation import evaluate_questions, load_evaluation_questions
from llm_wiki.search import (
    SearchResult,
    embed_query_with_retry,
    hybrid_search,
    keyword_search,
    vector_search,
)
from llm_wiki.search_scope import infer_search_scope

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evaluation/results-v2-evidence.json"
METHODS = ("keyword", "vector", "hybrid")
# Fixed before the run: all five current-policy questions plus three previously
# successful vector questions covering exact, paraphrase and cross-reference.
ANSWER_CASES = (
    "current-05",
    "exact-01",
    "paraphrase-02",
    "current-01",
    "current-02",
    "current-03",
    "current-04",
    "cross-02",
)


def representatives(results):
    seen = set()
    selected = []
    for result in results:
        if result.document_id not in seen:
            selected.append(result)
            seen.add(result.document_id)
    return selected


def save(payload):
    completed = {
        answer["question_id"]
        for answer in payload["answers"]
        if set(answer["variants"]) == {"1", "2"}
    }
    payload["remaining_answer_sample_ids"] = [
        question_id for question_id in payload["answer_sample_ids"] if question_id not in completed
    ]
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def generate_answers(payload, generator):
    cases = {case["question_id"]: case for case in payload["diagnostics"]}
    last_start = time.perf_counter() - 16
    for question_id in payload["answer_sample_ids"]:
        case = cases[question_id]
        existing = next((a for a in payload["answers"] if a["question_id"] == question_id), None)
        if existing is None:
            existing = {"question_id": question_id, "query": case["query"], "variants": {}}
            payload["answers"].append(existing)
        for count in (1, 2):
            if str(count) in existing["variants"]:
                continue
            # Stay below the observed five generation requests per minute.
            time.sleep(max(0, 16 - (time.perf_counter() - last_start)))
            sources = [SearchResult(**row) for row in case["sources"][f"vector_{count}"]]
            last_start = time.perf_counter()
            try:
                result = answer_with_retry(
                    generator, case["query"], sources, max_attempts=3, base_delay_seconds=15
                )
            except GenerationRequestError as exc:
                payload["answer_evaluation_status"] = "incomplete"
                payload["generation_error"] = {
                    "question_id": question_id,
                    "chunks_per_document": count,
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
                save(payload)
                raise
            existing["variants"][str(count)] = {
                "answer": result.answer,
                "source_refs": [
                    {"document_id": r.document_id, "chunk_index": r.chunk_index} for r in sources
                ],
                "prompt_characters": len(build_grounded_prompt(case["query"], sources)),
                "generation_seconds": time.perf_counter() - last_start,
            }
            payload["generation_operations"] = sum(len(a["variants"]) for a in payload["answers"])
            save(payload)
            print(f"answered={question_id} chunks_per_document={count}", flush=True)
    payload["answer_evaluation_status"] = "complete"
    payload.pop("generation_error", None)
    save(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resume", action="store_true", help="Resume saved source/answer comparison."
    )
    args = parser.parse_args()
    load_dotenv()
    if args.resume:
        payload = json.loads(OUTPUT.read_text())
        settings = GenerationSettings.from_env()
        if settings.model != payload["generation_model"]:
            parser.error("Resume must use the same generation model.")
        generate_answers(payload, GeminiAnswerProvider(settings))
        print(f"results={OUTPUT}")
        return
    questions = load_evaluation_questions(ROOT / "evaluation/questions.yaml")
    embedding_settings = EmbeddingSettings.from_env()
    generation_settings = GenerationSettings.from_env()
    embedder = GeminiEmbeddingProvider(embedding_settings)
    generator = GeminiAnswerProvider(generation_settings)
    rankings = {f"{method}_{count}": {} for method in METHODS for count in (1, 2)}
    diagnostics = []
    with connect_database(DatabaseSettings.from_env()) as connection:
        for question in questions:
            query = question.query
            vector = embed_query_with_retry(embedder, query)
            candidates = {}
            timings = {}
            for count in (1, 2):
                options = {
                    "limit": 3,
                    "scope": infer_search_scope(query),
                    "chunks_per_document": count,
                }
                for method in METHODS:
                    start = time.perf_counter()
                    if method == "keyword":
                        results = keyword_search(connection, query, **options)
                    elif method == "vector":
                        results = vector_search(connection, vector, **options)
                    else:
                        results = hybrid_search(connection, query, vector, vector_weight=1.0, **options)
                    variant = f"{method}_{count}"
                    timings[variant] = time.perf_counter() - start
                    candidates[variant] = results
                    rankings[variant][query] = results
            checks = {}
            for method in METHODS:
                before = candidates[f"{method}_1"]
                after = candidates[f"{method}_2"]
                checks[method] = {
                    "same_document_order": [r.document_id for r in before]
                    == [r.document_id for r in representatives(after)],
                    "original_chunks_preserved": {(r.document_id, r.chunk_index) for r in before}
                    <= {(r.document_id, r.chunk_index) for r in after},
                }
                assert all(checks[method].values()), (question.question_id, method)
            diagnostics.append(
                {
                    "question_id": question.question_id,
                    "query": query,
                    "checks": checks,
                    "retrieval_seconds": timings,
                    "sources": {
                        key: [asdict(r) for r in value] for key, value in candidates.items()
                    },
                }
            )
            print(f"retrieved={question.question_id} document_order=preserved", flush=True)

    reports = [
        evaluate_questions(
            questions,
            lambda query, top_k, key=variant: representatives(rankings[key][query])[:top_k],
            method=variant,
        )
        for variant in rankings
    ]
    payload = {
        "question_set": "evaluation/questions.yaml",
        "top_k_documents": 3,
        "chunks_per_document": [1, 2],
        "reranker": False,
        "embedding_model": embedding_settings.model,
        "embedding_dimensions": embedding_settings.dimensions,
        "generation_model": generation_settings.model,
        "generation_temperature": 0,
        "embedding_operations": len(questions),
        "generation_operations": 0,
        "answer_evaluation_status": "incomplete",
        "answer_sample_ids": ANSWER_CASES,
        "measurement_note": "One pass, not p50/p95. Query embedding reused for each pair. "
        "Identical generation prompt template/model; only supplied sources differ. "
        "Retries may add HTTP requests. Prompt characters are not token counts. "
        "Document Hit does not measure answer correctness. Answers retained for manual review. "
        "Generation requests are paced at least 16 seconds apart; pacing is excluded from timing. "
        "An initial unrecorded attempt stopped on a per-minute quota error.",
        "reports": [r.to_dict() for r in reports],
        "diagnostics": diagnostics,
        "answers": [],
    }
    save(payload)
    generate_answers(payload, generator)
    for report in reports:
        print_report(report)
    print(f"results={OUTPUT}")


if __name__ == "__main__":
    main()
