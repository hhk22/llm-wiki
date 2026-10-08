"""MCP tools backed by the LLM Wiki HTTP API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

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

    @server.tool()
    async def submit_answer_feedback(
        answer_id: str, rating: Literal["helpful", "unhelpful"],
        category: Literal["interpretation", "retrieval", "answer", "latency", "other"] = "other",
        comment: str = "",
    ) -> dict[str, Any]:
        """Record user feedback for an answer ID; does not modify Wiki or establish truth."""
        return await client_factory().feedback(answer_id, rating, category, comment)

    @server.tool()
    async def get_answer_record(answer_id: str) -> dict[str, Any]:
        """Read a saved answer, original request, trace and feedback by ID."""
        return await client_factory().get_answer(answer_id)

    return server


mcp = create_mcp_server()
