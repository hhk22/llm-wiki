from __future__ import annotations

from dataclasses import replace

from llm_wiki.chunking import chunk_document
from llm_wiki.documents import Document
from llm_wiki.indexing import calculate_content_hash
from llm_wiki.storage_validation import StoredChunk, StoredDocument, evaluate_storage

MODEL = "gemini-embedding-2"
DIMENSIONS = 768


def make_document() -> Document:
    return Document(
        document_id="guide-v1",
        title="배포 가이드 v1",
        topic="deploy-guide",
        source_path="deploy-guide/v1.md",
        metadata={"version": "1"},
        body="# 배포 가이드 v1\n\n변경 이유\n\n## 현재 규칙\n\n금요일 배포 금지",
    )


def make_stored_rows(document: Document) -> tuple[list[StoredDocument], list[StoredChunk]]:
    content_hash = calculate_content_hash(
        document,
        embedding_model=MODEL,
        embedding_dimensions=DIMENSIONS,
    )
    chunks = [
        StoredChunk(
            document_id=chunk.document_id,
            chunk_index=chunk.chunk_index,
            heading_path=chunk.heading_path,
            content=chunk.content,
            embedding_dimensions=DIMENSIONS,
            has_tsv=True,
        )
        for chunk in chunk_document(document)
    ]
    return [StoredDocument(document.document_id, content_hash)], chunks


def test_matching_storage_passes_validation() -> None:
    document = make_document()
    stored_documents, stored_chunks = make_stored_rows(document)

    result = evaluate_storage(
        [document],
        stored_documents,
        stored_chunks,
        embedding_model=MODEL,
        embedding_dimensions=DIMENSIONS,
    )

    assert result.is_valid
    assert result.source_documents == 1
    assert result.expected_chunks == 2
    assert result.stored_tsv == 2
    assert result.minimum_embedding_dimensions == DIMENSIONS
    assert result.maximum_embedding_dimensions == DIMENSIONS


def test_stale_and_incomplete_storage_fails_validation() -> None:
    document = make_document()
    _, stored_chunks = make_stored_rows(document)
    stale_document = StoredDocument(document.document_id, "0" * 64)
    invalid_chunk = replace(
        stored_chunks[0],
        content="오래된 내용",
        embedding_dimensions=384,
        has_tsv=False,
    )

    result = evaluate_storage(
        [document],
        [stale_document],
        [invalid_chunk],
        embedding_model=MODEL,
        embedding_dimensions=DIMENSIONS,
    )

    assert not result.is_valid
    assert result.content_hash_mismatches == 1
    assert result.missing_chunks == 1
    assert result.chunk_content_mismatches == 1
    assert result.invalid_embedding_dimensions == 1
    assert result.stored_tsv == 0
