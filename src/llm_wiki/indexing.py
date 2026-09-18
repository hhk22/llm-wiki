"""Incrementally embed and store Markdown document chunks."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from psycopg import Connection

from llm_wiki.chunking import chunk_document
from llm_wiki.documents import Document
from llm_wiki.embedding import EmbeddingRequestError

CHUNKING_VERSION = "heading-v1"
EMBEDDING_INPUT_VERSION = "document-v1"
MAX_EMBEDDING_ATTEMPTS = 5
RETRY_BASE_DELAY_SECONDS = 2.0

UPSERT_DOCUMENT_SQL = """
INSERT INTO documents (
    id,
    source_path,
    title,
    topic,
    version,
    updated_at,
    content_hash
)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    source_path = EXCLUDED.source_path,
    title = EXCLUDED.title,
    topic = EXCLUDED.topic,
    version = EXCLUDED.version,
    updated_at = EXCLUDED.updated_at,
    content_hash = EXCLUDED.content_hash
"""

INSERT_CHUNK_SQL = """
INSERT INTO chunks (
    document_id,
    chunk_index,
    heading_path,
    content,
    embedding
)
VALUES (%s, %s, %s, %s, %s::vector)
"""


class DocumentEmbeddingProvider(Protocol):
    def embed_document(self, content: str, *, title: str | None = None) -> list[float]: ...


ProgressCallback = Callable[[str, str, int], None]


@dataclass(frozen=True)
class IngestResult:
    indexed_documents: int
    skipped_documents: int
    indexed_chunks: int


def calculate_content_hash(
    document: Document,
    *,
    embedding_model: str,
    embedding_dimensions: int,
) -> str:
    """Hash every input that can change chunk or embedding output."""
    normalized_body = "\n".join(document.body.splitlines()).strip()
    payload = {
        "body": normalized_body,
        "chunking_version": CHUNKING_VERSION,
        "embedding_dimensions": embedding_dimensions,
        "embedding_input_version": EMBEDDING_INPUT_VERSION,
        "embedding_model": embedding_model,
        "title": document.title.strip(),
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def ingest_documents(
    connection: Connection[Any],
    documents: Iterable[Document],
    provider: DocumentEmbeddingProvider,
    *,
    embedding_model: str,
    embedding_dimensions: int,
    on_progress: ProgressCallback | None = None,
) -> IngestResult:
    """Index new or changed documents and skip unchanged embeddings."""
    indexed_documents = 0
    skipped_documents = 0
    indexed_chunks = 0

    for document in documents:
        content_hash = calculate_content_hash(
            document,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
        )
        stored = connection.execute(
            "SELECT content_hash FROM documents WHERE id = %s",
            (document.document_id,),
        ).fetchone()
        connection.commit()

        if stored and stored[0] == content_hash:
            with connection.transaction():
                _upsert_document(connection, document, content_hash)
            skipped_documents += 1
            if on_progress:
                on_progress("skipped", document.document_id, 0)
            continue

        chunks = chunk_document(document)
        embeddings = [
            embed_document_with_retry(provider, chunk.content, title=chunk.title)
            for chunk in chunks
        ]
        _validate_embeddings(embeddings, embedding_dimensions)

        with connection.transaction():
            _upsert_document(connection, document, content_hash)
            connection.execute(
                "DELETE FROM chunks WHERE document_id = %s",
                (document.document_id,),
            )
            rows = [
                (
                    chunk.document_id,
                    chunk.chunk_index,
                    chunk.heading_path,
                    chunk.content,
                    _serialize_vector(embedding),
                )
                for chunk, embedding in zip(chunks, embeddings, strict=True)
            ]
            with connection.cursor() as cursor:
                cursor.executemany(INSERT_CHUNK_SQL, rows)

        indexed_documents += 1
        indexed_chunks += len(chunks)
        if on_progress:
            on_progress("indexed", document.document_id, len(chunks))

    return IngestResult(
        indexed_documents=indexed_documents,
        skipped_documents=skipped_documents,
        indexed_chunks=indexed_chunks,
    )


def embed_document_with_retry(
    provider: DocumentEmbeddingProvider,
    content: str,
    *,
    title: str,
    max_attempts: int = MAX_EMBEDDING_ATTEMPTS,
    base_delay_seconds: float = RETRY_BASE_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> list[float]:
    """Retry only rate-limit and server-side embedding failures."""
    for attempt in range(1, max_attempts + 1):
        try:
            return provider.embed_document(content, title=title)
        except EmbeddingRequestError as exc:
            if not exc.retryable or attempt == max_attempts:
                raise
            sleep(base_delay_seconds * (2 ** (attempt - 1)))

    raise AssertionError("Embedding retry loop ended unexpectedly.")


def _upsert_document(
    connection: Connection[Any],
    document: Document,
    content_hash: str,
) -> None:
    connection.execute(
        UPSERT_DOCUMENT_SQL,
        (
            document.document_id,
            document.source_path,
            document.title,
            document.topic,
            _optional_metadata(document, "version"),
            _optional_date_metadata(document, "updated_at"),
            content_hash,
        ),
    )


def _optional_metadata(document: Document, key: str) -> str | None:
    value = document.metadata.get(key)
    return str(value).strip() if value is not None and str(value).strip() else None


def _optional_date_metadata(document: Document, key: str) -> date | None:
    value = _optional_metadata(document, key)
    return date.fromisoformat(value) if value else None


def _validate_embeddings(embeddings: Sequence[Sequence[float]], dimensions: int) -> None:
    if any(len(embedding) != dimensions for embedding in embeddings):
        raise ValueError(f"Every embedding must have {dimensions} dimensions.")


def _serialize_vector(embedding: Sequence[float]) -> str:
    return "[" + ",".join(repr(value) for value in embedding) + "]"
