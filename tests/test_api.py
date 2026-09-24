from __future__ import annotations

from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient

from llm_wiki import api
from llm_wiki.api import create_app
from llm_wiki.search import SearchResult


def search_result() -> SearchResult:
    return SearchResult(
        document_id="deploy-guide-v30",
        chunk_index=1,
        title="배포 가이드 v30",
        topic="deploy-guide",
        version="30",
        heading_path="배포 가이드 v30 > 현재 규칙",
        content="배포 금지 시간: 목요일 오후",
        score=0.91,
    )


class FakeService:
    def health(self) -> None:
        return None

    def search(self, query: str, method: str, top_k: int) -> list[SearchResult]:
        self.last_call = (query, method, top_k)
        return [search_result()]

    def answer(self, query: str, method: str, top_k: int) -> tuple[str, list[SearchResult]]:
        self.last_call = (query, method, top_k)
        return "목요일 오후입니다. [1]", [search_result()]


def test_health_endpoint() -> None:
    client = TestClient(create_app(FakeService()))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_search_endpoint_returns_ranked_documents() -> None:
    client = TestClient(create_app(FakeService()))

    response = client.post(
        "/search",
        json={"query": "배포 금지 시간", "method": "vector", "top_k": 3},
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["document_id"] == "deploy-guide-v30"
    assert response.json()["results"][0]["score"] == 0.91


def test_answer_endpoint_returns_text_and_sources() -> None:
    client = TestClient(create_app(FakeService()))

    response = client.post(
        "/answer",
        json={"query": "언제 배포할 수 없나요?", "method": "vector", "top_k": 3},
    )

    assert response.status_code == 200
    assert response.json()["answer"].endswith("[1]")
    assert response.json()["sources"][0]["document_id"] == "deploy-guide-v30"


def test_query_validation_rejects_blank_text_and_large_top_k() -> None:
    client = TestClient(create_app(FakeService()))

    blank = client.post("/search", json={"query": "   "})
    large_top_k = client.post("/search", json={"query": "query", "top_k": 11})

    assert blank.status_code == 422
    assert large_top_k.status_code == 422


@pytest.mark.parametrize("path", ["/search", "/answer"])
def test_hybrid_requests_reach_service_and_preserve_original_query(path: str) -> None:
    service = FakeService()
    client = TestClient(create_app(service))

    response = client.post(path, json={"query": "E-008은?", "method": "hybrid", "top_k": 3})

    assert response.status_code == 200
    assert response.json()["method"] == "hybrid"
    assert response.json()["query"] == "E-008은?"
    assert service.last_call == ("E-008은?", "hybrid", 3)
    assert client.post(path, json={"query": "test", "method": "unknown"}).status_code == 422


def test_wiki_service_embeds_original_query_once_and_routes_to_hybrid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = object()
    queries = []
    calls = []

    class Provider:
        def embed_query(self, query: str) -> list[float]:
            queries.append(query)
            return [0.1, 0.2]

    def hybrid(conn, query, vector, *, limit, chunks_per_document):
        assert chunks_per_document == 1
        calls.append((conn, query, vector, limit))
        return [search_result()]

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(api, "connect_database", lambda settings: nullcontext(connection))
    monkeypatch.setattr(api, "GeminiEmbeddingProvider", lambda settings: Provider())
    monkeypatch.setattr(api, "hybrid_search", hybrid)

    assert api.WikiService().search("E-008은?", "hybrid", 3) == [search_result()]
    assert queries == ["E-008은?"]
    assert calls == [(connection, "E-008은?", [0.1, 0.2], 3)]


@pytest.mark.parametrize(
    ("query", "mode", "version"),
    [
        ("현재 프로덕션 배포 명령은?", "latest", None),
        ("배포 가이드 v22의 금지 시간은?", "version", 22),
        ("현재 배포 정책이 바뀐 계기는?", "all", None),
    ],
)
def test_vector_service_passes_scope_and_preserves_embedding_query(
    monkeypatch,
    query,
    mode,
    version,
):
    connection = object()
    embedded = []
    scopes = []

    class Provider:
        def embed_query(self, text):
            embedded.append(text)
            return [0.1, 0.2]

    def vectors(conn, vector, *, limit, scope, chunks_per_document):
        assert chunks_per_document == 1
        assert conn is connection
        assert vector == [0.1, 0.2]
        assert limit == 3
        scopes.append(scope)
        return [search_result()]

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(api, "connect_database", lambda settings: nullcontext(connection))
    monkeypatch.setattr(api, "GeminiEmbeddingProvider", lambda settings: Provider())
    monkeypatch.setattr(api, "vector_search", vectors)
    monkeypatch.setattr(api, "follow_causal_reference", lambda conn, query, results, **kw: results)

    assert api.WikiService().search(query, "vector", 3) == [search_result()]
    assert embedded == [query]
    assert scopes[0].mode == mode
    assert scopes[0].version == version


@pytest.mark.parametrize("method", ["keyword", "vector", "hybrid"])
def test_answer_uses_two_chunks_per_document_and_returns_numbered_sources(monkeypatch, method):
    from dataclasses import replace

    from llm_wiki.answering import build_grounded_prompt

    chunks = [replace(search_result(), chunk_index=0, content="변경 없음."), search_result()]
    calls = []

    def retrieve(self, query, selected_method, top_k, *, chunks_per_document=1):
        calls.append((selected_method, top_k, chunks_per_document))
        return chunks

    class Provider:
        def generate(self, query, sources):
            assert sources == chunks
            prompt = build_grounded_prompt(query, sources)
            assert "[1] document_id=deploy-guide-v30" in prompt
            assert "[2] document_id=deploy-guide-v30" in prompt
            assert "변경 없음." in prompt and "배포 금지 시간: 목요일 오후" in prompt
            return "목요일 오후입니다. [2]"

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(api.WikiService, "search", retrieve)
    monkeypatch.setattr(api, "GeminiAnswerProvider", lambda settings: Provider())
    client = TestClient(create_app(api.WikiService()))
    response = client.post(
        "/answer", json={"query": "현재 배포 금지 시간은?", "method": method, "top_k": 1}
    )
    assert response.status_code == 200
    assert calls == [(method, 1, 2)]
    assert response.json()["answer"] == "목요일 오후입니다. [2]"
    assert [r["chunk_index"] for r in response.json()["sources"]] == [0, 1]
