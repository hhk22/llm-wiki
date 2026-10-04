from __future__ import annotations

import asyncio
import json

import httpx

from llm_wiki.api_client import WikiApiClient


def test_api_client_posts_search_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert request.method == "POST"
        return httpx.Response(
            200,
            json={"query": "query", "method": "vector", "results": []},
        )

    client = WikiApiClient(
        "http://wiki.test",
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(client.search("query", "vector", 3))

    assert result["query"] == "query"
    assert result["results"] == []


def test_api_client_raises_for_api_error() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(503, json={"detail": "down"}))
    client = WikiApiClient("http://wiki.test", transport=transport)

    try:
        asyncio.run(client.answer("query", "vector", 3))
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 503
    else:
        raise AssertionError("HTTPStatusError was not raised")


def test_api_client_forwards_wiki_history_and_budget():
    history = [{"role": "user", "content": "배포 금지 시간은?"}]

    def handler(request):
        assert request.url.path == "/answer"
        payload = json.loads(request.content)
        assert payload["method"] == "wiki"
        assert payload["history"] == history
        assert payload["max_input_tokens"] == 9000
        return httpx.Response(200, json={"status": "clarification_required", "sources": []})

    client = WikiApiClient("http://wiki.test", transport=httpx.MockTransport(handler))
    result = asyncio.run(client.answer("그 전에는?", "wiki", 3,
                                      history=history, max_input_tokens=9000))
    assert result["status"] == "clarification_required"
