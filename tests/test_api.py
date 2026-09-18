from __future__ import annotations

from fastapi.testclient import TestClient

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
        return [search_result()]

    def answer(self, query: str, method: str, top_k: int) -> tuple[str, list[SearchResult]]:
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
