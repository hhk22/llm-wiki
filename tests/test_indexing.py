from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from llm_wiki.documents import load_document
from llm_wiki.embedding import EmbeddingRequestError
from llm_wiki.indexing import calculate_content_hash, embed_document_with_retry

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "sources"


class FlakyEmbeddingProvider:
    def __init__(self, failures: int, *, retryable: bool = True) -> None:
        self.failures = failures
        self.retryable = retryable
        self.calls = 0

    def embed_document(self, content: str, *, title: str | None = None) -> list[float]:
        self.calls += 1
        if self.calls <= self.failures:
            raise EmbeddingRequestError("temporary failure", retryable=self.retryable)
        return [0.1, 0.2, 0.3]


def test_content_hash_is_deterministic() -> None:
    document = load_document(SOURCE_ROOT / "deploy-guide" / "v22.md", source_root=SOURCE_ROOT)

    first = calculate_content_hash(
        document,
        embedding_model="gemini-embedding-2",
        embedding_dimensions=768,
    )
    second = calculate_content_hash(
        document,
        embedding_model="gemini-embedding-2",
        embedding_dimensions=768,
    )

    assert first == second
    assert len(first) == 64


def test_content_hash_changes_with_indexing_input() -> None:
    document = load_document(SOURCE_ROOT / "deploy-guide" / "v22.md", source_root=SOURCE_ROOT)
    changed_document = replace(document, body=f"{document.body}\n\n- 추가 규칙")

    original_hash = calculate_content_hash(
        document,
        embedding_model="gemini-embedding-2",
        embedding_dimensions=768,
    )
    changed_body_hash = calculate_content_hash(
        changed_document,
        embedding_model="gemini-embedding-2",
        embedding_dimensions=768,
    )
    changed_model_hash = calculate_content_hash(
        document,
        embedding_model="another-model",
        embedding_dimensions=768,
    )

    assert len({original_hash, changed_body_hash, changed_model_hash}) == 3


def test_content_hash_ignores_non_embedding_metadata() -> None:
    document = load_document(SOURCE_ROOT / "deploy-guide" / "v22.md", source_root=SOURCE_ROOT)
    changed_metadata = {**document.metadata, "updated_at": "2026-01-01"}
    metadata_only_change = replace(document, metadata=changed_metadata)

    original_hash = calculate_content_hash(
        document,
        embedding_model="gemini-embedding-2",
        embedding_dimensions=768,
    )
    metadata_hash = calculate_content_hash(
        metadata_only_change,
        embedding_model="gemini-embedding-2",
        embedding_dimensions=768,
    )

    assert original_hash == metadata_hash


def test_embedding_retries_transient_failures() -> None:
    provider = FlakyEmbeddingProvider(failures=2)
    delays: list[float] = []

    vector = embed_document_with_retry(
        provider,
        "content",
        title="title",
        max_attempts=3,
        base_delay_seconds=1,
        sleep=delays.append,
    )

    assert vector == [0.1, 0.2, 0.3]
    assert provider.calls == 3
    assert delays == [1, 2]


def test_embedding_does_not_retry_non_transient_failure() -> None:
    provider = FlakyEmbeddingProvider(failures=1, retryable=False)

    with pytest.raises(EmbeddingRequestError):
        embed_document_with_retry(
            provider,
            "content",
            title="title",
            sleep=lambda _: None,
        )

    assert provider.calls == 1
