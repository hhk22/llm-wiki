"""Compare indexed PostgreSQL rows with the current Markdown sources."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from llm_wiki.chunking import chunk_documents
from llm_wiki.documents import Document
from llm_wiki.indexing import calculate_content_hash

CONTENT_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class StorageReader(Protocol):
    def execute(self, query: str, params: Any = None) -> Any: ...


@dataclass(frozen=True)
class StoredDocument:
    document_id: str
    content_hash: str


@dataclass(frozen=True)
class StoredChunk:
    document_id: str
    chunk_index: int
    heading_path: str
    content: str
    embedding_dimensions: int
    has_tsv: bool


@dataclass(frozen=True)
class StorageValidationResult:
    source_documents: int
    expected_chunks: int
    stored_documents: int
    stored_chunks: int
    stored_tsv: int
    minimum_embedding_dimensions: int | None
    maximum_embedding_dimensions: int | None
    invalid_content_hashes: int
    duplicate_chunks: int
    missing_documents: int
    unexpected_documents: int
    content_hash_mismatches: int
    missing_chunks: int
    unexpected_chunks: int
    chunk_content_mismatches: int
    invalid_embedding_dimensions: int

    @property
    def is_valid(self) -> bool:
        return all(
            value == 0
            for value in (
                self.source_documents - self.stored_documents,
                self.expected_chunks - self.stored_chunks,
                self.invalid_content_hashes,
                self.duplicate_chunks,
                self.missing_documents,
                self.unexpected_documents,
                self.content_hash_mismatches,
                self.missing_chunks,
                self.unexpected_chunks,
                self.chunk_content_mismatches,
                self.invalid_embedding_dimensions,
                self.stored_chunks - self.stored_tsv,
            )
        )


def validate_storage(
    connection: StorageReader,
    documents: Iterable[Document],
    *,
    embedding_model: str,
    embedding_dimensions: int,
) -> StorageValidationResult:
    """Load stored rows and compare them with freshly parsed source documents."""
    source_documents = list(documents)
    stored_documents = [
        StoredDocument(document_id=row[0], content_hash=row[1])
        for row in connection.execute(
            "SELECT id, content_hash FROM documents ORDER BY id"
        ).fetchall()
    ]
    stored_chunks = [
        StoredChunk(
            document_id=row[0],
            chunk_index=row[1],
            heading_path=row[2],
            content=row[3],
            embedding_dimensions=row[4],
            has_tsv=row[5],
        )
        for row in connection.execute(
            """
            SELECT
                document_id,
                chunk_index,
                heading_path,
                content,
                vector_dims(embedding),
                tsv IS NOT NULL
            FROM chunks
            ORDER BY document_id, chunk_index
            """
        ).fetchall()
    ]
    return evaluate_storage(
        source_documents,
        stored_documents,
        stored_chunks,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
    )


def evaluate_storage(
    documents: Sequence[Document],
    stored_documents: Sequence[StoredDocument],
    stored_chunks: Sequence[StoredChunk],
    *,
    embedding_model: str,
    embedding_dimensions: int,
) -> StorageValidationResult:
    """Return integrity metrics for source documents and stored rows."""
    expected_document_hashes = {
        document.document_id: calculate_content_hash(
            document,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
        )
        for document in documents
    }
    stored_document_hashes = {
        document.document_id: document.content_hash for document in stored_documents
    }

    expected_chunks = {
        (chunk.document_id, chunk.chunk_index): (chunk.heading_path, chunk.content)
        for chunk in chunk_documents(documents)
    }
    stored_chunk_keys = [(chunk.document_id, chunk.chunk_index) for chunk in stored_chunks]
    stored_chunk_counts = Counter(stored_chunk_keys)
    stored_chunk_values = {
        (chunk.document_id, chunk.chunk_index): (chunk.heading_path, chunk.content)
        for chunk in stored_chunks
    }

    expected_document_ids = set(expected_document_hashes)
    stored_document_ids = set(stored_document_hashes)
    expected_chunk_keys = set(expected_chunks)
    actual_chunk_keys = set(stored_chunk_values)
    dimensions = [chunk.embedding_dimensions for chunk in stored_chunks]

    return StorageValidationResult(
        source_documents=len(documents),
        expected_chunks=len(expected_chunks),
        stored_documents=len(stored_documents),
        stored_chunks=len(stored_chunks),
        stored_tsv=sum(chunk.has_tsv for chunk in stored_chunks),
        minimum_embedding_dimensions=min(dimensions, default=None),
        maximum_embedding_dimensions=max(dimensions, default=None),
        invalid_content_hashes=sum(
            CONTENT_HASH_PATTERN.fullmatch(document.content_hash) is None
            for document in stored_documents
        ),
        duplicate_chunks=sum(count - 1 for count in stored_chunk_counts.values() if count > 1),
        missing_documents=len(expected_document_ids - stored_document_ids),
        unexpected_documents=len(stored_document_ids - expected_document_ids),
        content_hash_mismatches=sum(
            stored_document_hashes.get(document_id) != expected_hash
            for document_id, expected_hash in expected_document_hashes.items()
            if document_id in stored_document_hashes
        ),
        missing_chunks=len(expected_chunk_keys - actual_chunk_keys),
        unexpected_chunks=len(actual_chunk_keys - expected_chunk_keys),
        chunk_content_mismatches=sum(
            stored_chunk_values[key] != expected_chunks[key]
            for key in expected_chunk_keys & actual_chunk_keys
        ),
        invalid_embedding_dimensions=sum(
            chunk.embedding_dimensions != embedding_dimensions for chunk in stored_chunks
        ),
    )
