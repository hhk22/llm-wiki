from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp import Client

from llm_wiki.mcp_server import create_mcp_server


class FakeApiClient:
    async def search(self, query: str, method: str, top_k: int) -> dict[str, Any]:
        return {
            "query": query,
            "method": method,
            "results": [{"document_id": "deploy-guide-v30"}],
        }

    async def answer(self, query: str, method: str, top_k: int) -> dict[str, Any]:
        return {
            "query": query,
            "method": method,
            "answer": "목요일 오후입니다. [1]",
            "sources": [{"document_id": "deploy-guide-v30"}],
        }


def test_mcp_search_tool_calls_api_client() -> None:
    async def call_tool() -> Any:
        server = create_mcp_server(lambda: FakeApiClient())
        async with Client(server) as client:
            return await client.call_tool(
                "search_wiki",
                {"query": "배포 금지 시간", "method": "vector", "top_k": 3},
            )

    result = asyncio.run(call_tool())

    assert result.structured_content["query"] == "배포 금지 시간"
    assert result.structured_content["results"][0]["document_id"] == "deploy-guide-v30"


def test_mcp_answer_tool_returns_sources() -> None:
    async def call_tool() -> Any:
        server = create_mcp_server(lambda: FakeApiClient())
        async with Client(server) as client:
            return await client.call_tool(
                "ask_wiki",
                {"query": "언제 배포할 수 없나요?"},
            )

    result = asyncio.run(call_tool())

    assert result.structured_content["answer"].endswith("[1]")
    assert result.structured_content["sources"][0]["document_id"] == "deploy-guide-v30"


@pytest.mark.parametrize("tool_name", ["search_wiki", "ask_wiki"])
def test_mcp_tools_accept_and_forward_hybrid_method(tool_name: str) -> None:
    async def call_tool() -> Any:
        async with Client(create_mcp_server(lambda: FakeApiClient())) as client:
            return await client.call_tool(
                tool_name, {"query": "E-021 오류 대응", "method": "hybrid", "top_k": 3}
            )

    result = asyncio.run(call_tool())

    assert result.structured_content["method"] == "hybrid"
    assert result.structured_content["query"] == "E-021 오류 대응"


def test_mcp_forwards_history_for_wiki_answers():
    history = [{"role": "user", "content": "배포 금지 시간은?"}]

    class ClientWithHistory:
        async def answer(self, query, method, top_k, **kwargs):
            assert (query, method, top_k) == ("그 전에는?", "wiki", 3)
            assert kwargs == {"history": history, "max_input_tokens": 9000}
            return {"status": "clarification_required", "answer": "어느 변경인가요?", "sources": []}

    async def run():
        async with Client(create_mcp_server(ClientWithHistory)) as client:
            return await client.call_tool("ask_wiki", {
                "query": "그 전에는?", "method": "wiki", "history": history,
                "max_input_tokens": 9000,
            })

    result = asyncio.run(run())
    assert result.structured_content["status"] == "clarification_required"
