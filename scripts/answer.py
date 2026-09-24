"""Retrieve document chunks and generate a source-grounded answer.

Run: uv run python scripts/answer.py "현재 배포 금지 시간은 언제인가요?"
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from llm_wiki.answering import GeminiAnswerProvider, GenerationSettings, answer_with_retry
from llm_wiki.database import DatabaseSettings, connect_database
from llm_wiki.embedding import EmbeddingSettings, GeminiEmbeddingProvider
from llm_wiki.references import follow_causal_reference
from llm_wiki.search import (
    ANSWER_CHUNKS_PER_DOCUMENT,
    embed_query_with_retry,
    hybrid_search,
    keyword_search,
    vector_search,
)
from llm_wiki.search_scope import infer_search_scope


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--method", choices=("keyword", "vector", "hybrid"), default="vector")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument(
        "--chunks-per-document", type=int, choices=(1, 2), default=ANSWER_CHUNKS_PER_DOCUMENT
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv()

    with connect_database(DatabaseSettings.from_env()) as connection:
        if args.method == "keyword":
            sources = keyword_search(
                connection,
                args.query,
                limit=args.top_k,
                chunks_per_document=args.chunks_per_document,
            )
        else:
            embedding_provider = GeminiEmbeddingProvider(EmbeddingSettings.from_env())
            query_vector = embed_query_with_retry(embedding_provider, args.query)
            if args.method == "hybrid":
                sources = hybrid_search(
                    connection,
                    args.query,
                    query_vector,
                    limit=args.top_k,
                    chunks_per_document=args.chunks_per_document,
                )
            else:
                sources = vector_search(
                    connection,
                    query_vector,
                    limit=args.top_k,
                    scope=infer_search_scope(args.query),
                    chunks_per_document=args.chunks_per_document,
                )

        sources = follow_causal_reference(
            connection,
            args.query,
            sources,
            limit=args.top_k,
            chunks_per_document=args.chunks_per_document,
        )

    answer_provider = GeminiAnswerProvider(GenerationSettings.from_env())
    result = answer_with_retry(answer_provider, args.query, sources)

    print(result.answer)
    print("\nSources:")
    for index, source in enumerate(result.sources, start=1):
        print(
            f"[{index}] {source.title} ({source.document_id}, chunk {source.chunk_index}) "
            f"score={source.score:.4f}"
        )


if __name__ == "__main__":
    main()
