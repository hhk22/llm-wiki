"""Async client used by MCP tools to call the LLM Wiki HTTP API."""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8000"


class WikiApiClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("LLM_WIKI_API_URL", DEFAULT_API_URL)).rstrip("/")
        self.transport = transport

    async def search(self, query: str, method: str, top_k: int) -> dict[str, Any]:
        return await self._post(
            "/search",
            {"query": query, "method": method, "top_k": top_k},
        )

    async def answer(
        self, query: str, method: str, top_k: int, *,
        history: list[dict[str, str]] | None = None, max_input_tokens: int = 48000,
    ) -> dict[str, Any]:
        payload = {"query": query, "method": method, "top_k": top_k}
        if history:
            payload["history"] = history
        payload["max_input_tokens"] = max_input_tokens
        return await self._post("/answer", payload)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=300,
            transport=self.transport,
        ) as client:
            response = await client.post(path, json=payload)
            response.raise_for_status()
            return response.json()
