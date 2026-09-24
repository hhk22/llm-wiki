"""MCP tools backed by the LLM Wiki HTTP API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from llm_wiki.api_client import WikiApiClient
from llm_wiki.search import SearchMethod

ApiClientFactory = Callable[[], WikiApiClient]


def create_mcp_server(client_factory: ApiClientFactory = WikiApiClient) -> MCPServer:
    server = MCPServer(
        "llm-wiki",
        instructions="Search the indexed wiki or answer a question with document sources.",
    )

    @server.tool()
    async def search_wiki(
        query: str,
        method: SearchMethod = "vector",
        top_k: int = 3,
    ) -> dict[str, Any]:
        """Search wiki documents and return ranked source chunks."""
        return await client_factory().search(query, method, top_k)

    @server.tool()
    async def ask_wiki(
        query: str,
        method: SearchMethod = "vector",
        top_k: int = 3,
    ) -> dict[str, Any]:
        """Answer a question using retrieved wiki documents and include sources."""
        return await client_factory().answer(query, method, top_k)

    return server


mcp = create_mcp_server()
