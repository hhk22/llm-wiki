from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from llm_wiki import search
from llm_wiki.embedding import EmbeddingRequestError
from llm_wiki.search import (
    SearchResult,
    embed_query_with_retry,
    fuse_rrf,
    hybrid_search,
    keyword_search,
    vector_search,
)


class ResultRows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class RecordingConnection:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.query = ""
        self.params: Any = None

    def execute(self, query: str, params: Any = None) -> ResultRows:
        self.query = query
        self.params = params
        return ResultRows(self.rows)


class FlakyQueryProvider:
    def __init__(self, failures: int, *, retryable: bool = True) -> None:
        self.failures = failures
        self.retryable = retryable
        self.calls = 0
        self.queries: list[str] = []

    def embed_query(self, query: str) -> list[float]:
        self.calls += 1
        self.queries.append(query)
        if self.calls <= self.failures:
            raise EmbeddingRequestError("temporary", retryable=self.retryable)
        return [0.1, 0.2]


def stored_row(document_id: str = "deploy-guide-v30") -> tuple[Any, ...]:
    return (
        document_id,
        1,
        "배포 가이드 v30",
        "deploy-guide",
        "30",
        "배포 가이드 v30 > 현재 규칙",
        "배포 금지 시간: 목요일 오후",
        0.8123,
    )


def test_keyword_search_maps_rows_and_uses_document_deduplication() -> None:
    connection = RecordingConnection([stored_row()])

    results = keyword_search(connection, "배포 금지 시간", limit=3)

    assert results[0].document_id == "deploy-guide-v30"
    assert results[0].score == pytest.approx(0.8123)
    assert connection.params == ("배포 금지 시간", 3, 1)
    assert "PARTITION BY document_id" in connection.query


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("E-008은 어떤 오류인가요?", "E-008 어떤 오류인가요?"),
        ("e-008에서는 어떤 설정을 확인하나요?", "e-008 어떤 설정을 확인하나요?"),
        ("E-008과 관련된 장애는?", "E-008 관련된 장애는?"),
        ("E-008은, E-021과 어떤 차이가 있나요?", "E-008, E-021 어떤 차이가 있나요?"),
        ("E-008은?", "E-008?"),
        ("  E-008은  ", "E-008"),
        ("E-021 오류 대응 방법", "E-021 오류 대응 방법"),
        ("커넥션은 언제 반환하나요?", "커넥션은 언제 반환하나요?"),
        ("E-0080은 어떤 오류인가요?", "E-0080은 어떤 오류인가요?"),
        ("SERVICE-E-008은 어떤 코드인가요?", "SERVICE-E-008은 어떤 코드인가요?"),
        ("E-008은하 설정", "E-008은하 설정"),
        ("v22에서 Q10의 규칙 확인", "v22에서 Q10의 규칙 확인"),
    ],
)
def test_keyword_search_normalizes_only_standalone_error_code_particles(
    query: str, expected: str
) -> None:
    connection = RecordingConnection([])

    keyword_search(connection, query, limit=3)

    assert connection.params == (expected, 3, 1)


def test_vector_query_embedding_preserves_error_code_particles() -> None:
    provider = FlakyQueryProvider(failures=0)

    embed_query_with_retry(provider, "E-008은 어떤 오류인가요?")

    assert provider.queries == ["E-008은 어떤 오류인가요?"]


def test_vector_search_serializes_vector_for_pgvector() -> None:
    connection = RecordingConnection([stored_row()])

    results = vector_search(connection, [0.1, 0.2], limit=5)

    assert results[0].chunk_index == 1
    assert connection.params == ("[0.1,0.2]", 5, 1)
    assert "<=>" in connection.query


def test_search_rejects_empty_input_and_invalid_limit() -> None:
    connection = RecordingConnection([])

    with pytest.raises(ValueError, match="query"):
        keyword_search(connection, "   ")
    with pytest.raises(ValueError, match="query_vector"):
        vector_search(connection, [])
    with pytest.raises(ValueError, match="limit"):
        keyword_search(connection, "query", limit=0)


def test_query_embedding_retries_transient_failure() -> None:
    provider = FlakyQueryProvider(failures=2)
    delays: list[float] = []

    vector = embed_query_with_retry(
        provider,
        "query",
        max_attempts=3,
        base_delay_seconds=1,
        sleep=delays.append,
    )

    assert vector == [0.1, 0.2]
    assert provider.calls == 3
    assert delays == [1, 2]


def candidate(document_id: str, *, score: float = 0.5, chunk: int = 0) -> SearchResult:
    return SearchResult(document_id, chunk, document_id, "test", None, "", "content", score)


def test_rrf_uses_ranks_and_prefers_vector_evidence_without_mutating_candidates() -> None:
    keyword = [candidate("a", score=1000), candidate("b", score=900)]
    vector = [candidate("b", score=0.9, chunk=2), candidate("c", score=0.8)]

    results = fuse_rrf(keyword, vector)

    assert [result.document_id for result in results] == ["b", "a", "c"]
    assert results[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert results[0].chunk_index == 2
    assert results[1].score == pytest.approx(1 / 61)
    assert results[2].score == pytest.approx(1 / 62)
    assert vector[0].score == 0.9
    assert keyword[1].chunk_index == 0


def test_rrf_swapped_ranks_tie_and_use_document_id_order() -> None:
    a, b = candidate("a"), candidate("b")

    results = fuse_rrf([b, a], [a, b])

    assert [result.document_id for result in results] == ["a", "b"]
    assert results[0].score == results[1].score
    assert fuse_rrf([b, a], [a, b], limit=1) == results[:1]


def test_rrf_counts_each_document_once_per_ranking() -> None:
    a, b = candidate("a"), candidate("b")

    results = fuse_rrf([a, replace(a, chunk_index=1), b], [])

    assert [result.document_id for result in results] == ["a", "b"]
    assert results[0].chunk_index == 0
    assert results[0].score == pytest.approx(1 / 61)
    assert results[1].score == pytest.approx(1 / 62)


def test_rrf_handles_empty_rankings_and_rejects_invalid_options() -> None:
    assert fuse_rrf([], []) == []
    assert fuse_rrf([], [candidate("b"), candidate("a")])[0].document_id == "b"
    with pytest.raises(ValueError, match="limit"):
        fuse_rrf([], [], limit=0)
    with pytest.raises(ValueError, match="rrf_k"):
        fuse_rrf([], [], rrf_k=0)


@pytest.mark.parametrize(("limit", "candidate_limit"), [(3, 10), (12, 12)])
def test_hybrid_collects_wider_candidates_before_fusion(
    monkeypatch: pytest.MonkeyPatch, limit: int, candidate_limit: int
) -> None:
    calls = []
    connection = RecordingConnection([])

    def keywords(
        conn: Any, query: str, *, limit: int, scope, chunks_per_document
    ) -> list[SearchResult]:
        assert scope == search.SearchScope()
        calls.append((conn, query, limit))
        return [candidate("a"), candidate("b")]

    def vectors(
        conn: Any, vector: Any, *, limit: int, scope, chunks_per_document
    ) -> list[SearchResult]:
        assert scope == search.SearchScope()
        calls.append((conn, vector, limit))
        return [candidate("b"), candidate("c")]

    monkeypatch.setattr(search, "keyword_search", keywords)
    monkeypatch.setattr(search, "vector_search", vectors)

    results = hybrid_search(connection, "E-008은?", [0.1, 0.2], limit=limit)

    assert calls == [
        (connection, "E-008은?", candidate_limit),
        (connection, [0.1, 0.2], candidate_limit),
    ]
    assert results[0].document_id == "b"


def test_keyword_baseline_evaluation_can_disable_normalization() -> None:
    connection = RecordingConnection([])

    keyword_search(connection, "E-008은?", normalize=False)

    assert connection.params == ("E-008은?", 3, 1)


@pytest.mark.parametrize("count", [0, -1, 3])
def test_retrieval_rejects_unsupported_chunk_limits(count):
    connection = RecordingConnection([])
    with pytest.raises(ValueError, match="chunks_per_document"):
        keyword_search(connection, "test", chunks_per_document=count)
    with pytest.raises(ValueError, match="chunks_per_document"):
        vector_search(connection, [1.0], chunks_per_document=count)
    with pytest.raises(ValueError, match="chunks_per_document"):
        hybrid_search(connection, "test", [1.0], chunks_per_document=count)


def test_hybrid_keeps_vector_chunks_and_keyword_only_fallback(monkeypatch):
    keyword = [
        candidate("a", chunk=0),
        candidate("a", chunk=1),
        candidate("b", chunk=0),
        candidate("b", chunk=1),
    ]
    vector = [candidate("a", chunk=2), candidate("a", chunk=3)]
    monkeypatch.setattr(search, "keyword_search", lambda *args, **kwargs: keyword)
    monkeypatch.setattr(search, "vector_search", lambda *args, **kwargs: vector)

    results = hybrid_search(RecordingConnection([]), "query", [1.0], chunks_per_document=2)

    assert [(r.document_id, r.chunk_index) for r in results] == [
        ("a", 2),
        ("a", 3),
        ("b", 0),
        ("b", 1),
    ]
    assert results[0].score == results[1].score == pytest.approx(4 / 61)
    assert results[2].score == results[3].score == pytest.approx(1 / 62)


def test_weighted_rrf_changes_preference_and_counts_each_document_once():
    a, b = candidate("a"), candidate("b")
    results = fuse_rrf([a, b], [b, replace(b, chunk_index=1), a], vector_weight=3)
    assert [r.document_id for r in results] == ["b", "a"]
    assert results[0].score == pytest.approx(1 / 62 + 3 / 61)
    assert results[1].score == pytest.approx(1 / 61 + 3 / 62)


@pytest.mark.parametrize("weight", [0, -1, float("nan"), float("inf")])
def test_rrf_rejects_invalid_weights(weight):
    with pytest.raises(ValueError, match="weights"):
        fuse_rrf([], [], vector_weight=weight)
