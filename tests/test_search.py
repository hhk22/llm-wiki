from __future__ import annotations

from typing import Any

import pytest

from llm_wiki.embedding import EmbeddingRequestError
from llm_wiki.search import embed_query_with_retry, keyword_search, vector_search


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
    assert connection.params == ("배포 금지 시간", 3)
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

    assert connection.params == (expected, 3)


def test_vector_query_embedding_preserves_error_code_particles() -> None:
    provider = FlakyQueryProvider(failures=0)

    embed_query_with_retry(provider, "E-008은 어떤 오류인가요?")

    assert provider.queries == ["E-008은 어떤 오류인가요?"]


def test_vector_search_serializes_vector_for_pgvector() -> None:
    connection = RecordingConnection([stored_row()])

    results = vector_search(connection, [0.1, 0.2], limit=5)

    assert results[0].chunk_index == 1
    assert connection.params == ("[0.1,0.2]", 5)
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
