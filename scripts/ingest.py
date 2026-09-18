"""Embed new or changed Markdown documents and store them in PostgreSQL.

Run: uv run python scripts/ingest.py
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from llm_wiki.database import DatabaseSettings, connect_database, initialize_schema
from llm_wiki.documents import load_documents
from llm_wiki.embedding import EmbeddingSettings, GeminiEmbeddingProvider
from llm_wiki.indexing import ingest_documents

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "sources"


def report_progress(action: str, document_id: str, chunk_count: int) -> None:
    if action == "indexed":
        print(f"indexed document={document_id} chunks={chunk_count}", flush=True)
    else:
        print(f"skipped document={document_id}", flush=True)


def main() -> None:
    load_dotenv()
    database_settings = DatabaseSettings.from_env()
    embedding_settings = EmbeddingSettings.from_env()
    provider = GeminiEmbeddingProvider(embedding_settings)
    documents = load_documents(SOURCE_ROOT)

    with connect_database(database_settings) as connection:
        initialize_schema(connection)
        connection.commit()
        result = ingest_documents(
            connection,
            documents,
            provider,
            embedding_model=embedding_settings.model,
            embedding_dimensions=embedding_settings.dimensions,
            on_progress=report_progress,
        )
        document_count, chunk_count, invalid_dimensions = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM documents),
                (SELECT count(*) FROM chunks),
                (SELECT count(*) FROM chunks WHERE vector_dims(embedding) != %s)
            """,
            (embedding_settings.dimensions,),
        ).fetchone()

    print(f"indexed_documents={result.indexed_documents}")
    print(f"skipped_documents={result.skipped_documents}")
    print(f"indexed_chunks={result.indexed_chunks}")
    print(f"stored_documents={document_count}")
    print(f"stored_chunks={chunk_count}")
    print(f"invalid_embedding_dimensions={invalid_dimensions}")


if __name__ == "__main__":
    main()
