"""Gemini embedding provider for document retrieval."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types

GEMINI_EMBEDDING_MODEL = "gemini-embedding-2"
EMBEDDING_DIMENSIONS = 768


class EmbeddingConfigurationError(ValueError):
    """Raised when embedding configuration is missing or invalid."""


class EmbeddingRequestError(RuntimeError):
    """Raised when the provider response cannot be used."""


@dataclass(frozen=True)
class EmbeddingSettings:
    api_key: str
    model: str = GEMINI_EMBEDDING_MODEL
    dimensions: int = EMBEDDING_DIMENSIONS

    @classmethod
    def from_env(cls) -> EmbeddingSettings:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise EmbeddingConfigurationError(
                "GEMINI_API_KEY is missing. Copy .env.example to .env and add a newly issued key."
            )
        return cls(api_key=api_key)


class GeminiEmbeddingProvider:
    """Create retrieval embeddings with Gemini Embedding 2."""

    def __init__(self, settings: EmbeddingSettings, client: Any | None = None) -> None:
        self.settings = settings
        self._client = client or genai.Client(api_key=settings.api_key)

    def embed_query(self, query: str) -> list[float]:
        """Embed a natural-language search query."""
        query = self._require_text(query, "query")
        content = f"task: search result | query: {query}"
        return self._embed(content)

    def embed_document(self, content: str, *, title: str | None = None) -> list[float]:
        """Embed a document chunk using Gemini's asymmetric retrieval format."""
        content = self._require_text(content, "content")
        document_title = title.strip() if title and title.strip() else "none"
        prepared = f"title: {document_title} | text: {content}"
        return self._embed(prepared)

    def _embed(self, content: str) -> list[float]:
        try:
            result = self._client.models.embed_content(
                model=self.settings.model,
                contents=content,
                config=types.EmbedContentConfig(
                    output_dimensionality=self.settings.dimensions,
                ),
            )
        except Exception as exc:  # Provider exceptions vary by transport and status code.
            raise EmbeddingRequestError("Gemini embedding request failed.") from exc

        if not result.embeddings or not result.embeddings[0].values:
            raise EmbeddingRequestError("Gemini returned no embedding values.")

        vector = list(result.embeddings[0].values)
        if len(vector) != self.settings.dimensions:
            raise EmbeddingRequestError(
                f"Expected {self.settings.dimensions} dimensions, received {len(vector)}."
            )
        if not all(math.isfinite(value) for value in vector):
            raise EmbeddingRequestError("Gemini returned a non-finite embedding value.")
        return vector

    @staticmethod
    def _require_text(value: str, field_name: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError(f"{field_name} must not be empty.")
        return value
