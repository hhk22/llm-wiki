"""Search indexed documents with keyword or vector retrieval.

Run: uv run python scripts/search.py "배포 금지 시간" --method vector
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from llm_wiki.database import DatabaseSettings, connect_database
from llm_wiki.embedding import EmbeddingSettings, GeminiEmbeddingProvider
from llm_wiki.search import embed_query_with_retry, keyword_search, vector_search


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--method", choices=("keyword", "vector"), default="vector")
    parser.add_argument("--top-k", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv()

    with connect_database(DatabaseSettings.from_env()) as connection:
        if args.method == "keyword":
            results = keyword_search(connection, args.query, limit=args.top_k)
        else:
            provider = GeminiEmbeddingProvider(EmbeddingSettings.from_env())
            query_vector = embed_query_with_retry(provider, args.query)
            results = vector_search(connection, query_vector, limit=args.top_k)

    print(f"method={args.method}")
    print(f"query={args.query}")
    for rank, result in enumerate(results, start=1):
        print(
            f"{rank}. document={result.document_id} chunk={result.chunk_index} "
            f"score={result.score:.4f} heading={result.heading_path}"
        )


if __name__ == "__main__":
    main()
