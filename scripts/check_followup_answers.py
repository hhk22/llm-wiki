"""Compare live before/after answers for the five follow-up failure cases.

Run: uv run python scripts/check_followup_answers.py [--resume]
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from llm_wiki.answering import GeminiAnswerProvider, GenerationSettings, answer_with_retry
from llm_wiki.database import DatabaseSettings, connect_database
from llm_wiki.embedding import EmbeddingSettings, GeminiEmbeddingProvider
from llm_wiki.evaluation import load_evaluation_questions
from llm_wiki.references import follow_causal_reference
from llm_wiki.search import (
    HYBRID_VECTOR_WEIGHT,
    SearchResult,
    embed_query_with_retry,
    hybrid_search,
    vector_search,
)
from llm_wiki.search_scope import infer_search_scope

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evaluation/followup-answer-check.json"
CASES = ("cross-01", "paraphrase-03", "cross-02", "cross-04", "cross-05")


def save(payload):
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    load_dotenv()
    generation = GenerationSettings.from_env()
    if args.resume:
        payload = json.loads(OUTPUT.read_text())
        if payload["generation_model"] != generation.model:
            parser.error("Resume must use the same model.")
    else:
        embedding = EmbeddingSettings.from_env()
        embedder = GeminiEmbeddingProvider(embedding)
        questions = {
            q.question_id: q for q in load_evaluation_questions(ROOT / "evaluation/questions.yaml")
        }
        payload = {
            "generation_model": generation.model,
            "embedding_model": embedding.model,
            "temperature": 0,
            "top_k_documents": 3,
            "chunks_per_document": 2,
            "note": "Live embeddings and retrieval; fixed development cases, not held-out accuracy. "
            "Same generation model/prompt template before and after. "
            "Case 2 adds a causal reference; case 3 changes RRF vector weight 1 -> 3. "
            "Document retrieval success does not imply answer improvement.",
            "status": "incomplete",
            "cases": [],
        }
        with connect_database(DatabaseSettings.from_env()) as connection:
            for question_id in CASES:
                query = questions[question_id].query
                vector = embed_query_with_retry(embedder, query)
                options = {"limit": 3, "chunks_per_document": 2, "scope": infer_search_scope(query)}
                if question_id == "cross-01":
                    before = vector_search(connection, vector, **options)
                    after = before
                    method = "vector"
                else:
                    before = hybrid_search(connection, query, vector, vector_weight=1.0, **options)
                    after = hybrid_search(
                        connection, query, vector, vector_weight=HYBRID_VECTOR_WEIGHT, **options
                    )
                    method = "hybrid"
                after = follow_causal_reference(
                    connection, query, after, limit=3, chunks_per_document=2
                )
                payload["cases"].append(
                    {
                        "question_id": question_id,
                        "query": query,
                        "method": method,
                        "sources": {
                            "before": [asdict(r) for r in before],
                            "after": [asdict(r) for r in after],
                        },
                        "answers": {},
                    }
                )
        save(payload)
    provider = GeminiAnswerProvider(generation)
    previous_start = time.perf_counter() - 16
    for case in payload["cases"]:
        for variant in ("before", "after"):
            if variant in case["answers"]:
                continue
            time.sleep(max(0, 16 - (time.perf_counter() - previous_start)))
            previous_start = time.perf_counter()
            sources = [SearchResult(**row) for row in case["sources"][variant]]
            answer = answer_with_retry(
                provider, case["query"], sources, max_attempts=3, base_delay_seconds=15
            )
            case["answers"][variant] = answer.answer
            save(payload)
            print(f"answered={case['question_id']} variant={variant}", flush=True)
    payload["status"] = "complete"
    save(payload)
    print(f"results={OUTPUT}")


if __name__ == "__main__":
    main()
