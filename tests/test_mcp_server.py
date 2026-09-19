from __future__ import annotations

import asyncio
from typing import Any

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
