"""Evaluate keyword and vector retrieval with the fixed question set.

Run: uv run python scripts/evaluate.py
"""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from llm_wiki.database import DatabaseSettings, connect_database
from llm_wiki.embedding import EmbeddingSettings, GeminiEmbeddingProvider
from llm_wiki.evaluation import EvaluationReport, evaluate_questions, load_evaluation_questions
from llm_wiki.search import embed_query_with_retry, keyword_search, vector_search

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = ROOT / "evaluation" / "questions.yaml"
RESULTS_PATH = ROOT / "evaluation" / "results-v1.json"


def main() -> None:
    load_dotenv()
    questions = load_evaluation_questions(QUESTIONS_PATH)
    embedding_settings = EmbeddingSettings.from_env()
    provider = GeminiEmbeddingProvider(embedding_settings)

    with connect_database(DatabaseSettings.from_env()) as connection:
        keyword_report = evaluate_questions(
            questions,
            lambda query, top_k: keyword_search(connection, query, limit=top_k),
            method="keyword",
        )
        vector_report = evaluate_questions(
            questions,
            lambda query, top_k: vector_search(
                connection,
                embed_query_with_retry(provider, query),
                limit=top_k,
            ),
            method="vector",
        )

    payload = {
        "question_set": QUESTIONS_PATH.relative_to(ROOT).as_posix(),
        "top_k": 3,
        "embedding_model": embedding_settings.model,
        "embedding_dimensions": embedding_settings.dimensions,
        "reports": [keyword_report.to_dict(), vector_report.to_dict()],
    }
    RESULTS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print_report(keyword_report)
    print_report(vector_report)
    print(f"results={RESULTS_PATH.relative_to(ROOT)}")


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
