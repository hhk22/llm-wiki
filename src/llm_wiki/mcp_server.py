"""MCP tools backed by the LLM Wiki HTTP API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from llm_wiki.api_client import WikiApiClient
from llm_wiki.conversation import AnswerMethod, ConversationTurn
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
        method: AnswerMethod = "vector",
        top_k: int = 3,
        history: list[ConversationTurn] | None = None,
        max_input_tokens: int = 48000,
    ) -> dict[str, Any]:
        """Answer with sources. Use method=wiki for Markdown navigation (max 3 pages).

        Pass recent user/assistant turns in history for follow-ups; no server-side memory.
        A clarification_required response asks the user to identify an ambiguous subject.
        """
        kwargs = {}
        if history:
            kwargs["history"] = [turn.model_dump() for turn in history]
        if max_input_tokens != 48000:
            kwargs["max_input_tokens"] = max_input_tokens
        return await client_factory().answer(query, method, top_k, **kwargs)

    return server


mcp = create_mcp_server()
