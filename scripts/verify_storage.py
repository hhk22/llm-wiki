"""Verify indexed PostgreSQL rows against the current Markdown sources.

Run: uv run python scripts/verify_storage.py
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from llm_wiki.database import DatabaseSettings, connect_database
from llm_wiki.documents import load_documents
from llm_wiki.embedding import EMBEDDING_DIMENSIONS, GEMINI_EMBEDDING_MODEL
from llm_wiki.storage_validation import validate_storage

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "sources"


def main() -> None:
    load_dotenv()
    documents = load_documents(SOURCE_ROOT)

    with connect_database(DatabaseSettings.from_env()) as connection:
        result = validate_storage(
            connection,
            documents,
            embedding_model=GEMINI_EMBEDDING_MODEL,
            embedding_dimensions=EMBEDDING_DIMENSIONS,
        )

    print(f"source_documents={result.source_documents}")
    print(f"expected_chunks={result.expected_chunks}")
    print(f"stored_documents={result.stored_documents}")
    print(f"stored_chunks={result.stored_chunks}")
    print(f"stored_tsv={result.stored_tsv}")
    print(
        "embedding_dimensions="
        f"{result.minimum_embedding_dimensions}/{result.maximum_embedding_dimensions}"
    )
    print(f"invalid_content_hashes={result.invalid_content_hashes}")
    print(f"duplicate_chunks={result.duplicate_chunks}")
    print(f"missing_documents={result.missing_documents}")
    print(f"unexpected_documents={result.unexpected_documents}")
    print(f"content_hash_mismatches={result.content_hash_mismatches}")
    print(f"missing_chunks={result.missing_chunks}")
    print(f"unexpected_chunks={result.unexpected_chunks}")
    print(f"chunk_content_mismatches={result.chunk_content_mismatches}")
    print(f"invalid_embedding_dimensions={result.invalid_embedding_dimensions}")
    print(f"status={'PASS' if result.is_valid else 'FAIL'}")

    if not result.is_valid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
