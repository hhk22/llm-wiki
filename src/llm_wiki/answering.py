"""Generate grounded answers from retrieved document chunks."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from google import genai
from google.genai import types

from llm_wiki.search import SearchResult

GEMINI_GENERATION_MODEL = "gemini-3.6-flash"


class GenerationConfigurationError(ValueError):
    """Raised when answer generation configuration is missing."""


class GenerationRequestError(RuntimeError):
    """Raised when Gemini does not return a usable answer."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class GenerationSettings:
    api_key: str
    model: str = GEMINI_GENERATION_MODEL

    @classmethod
    def from_env(cls) -> GenerationSettings:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        model = os.getenv("GEMINI_GENERATION_MODEL", GEMINI_GENERATION_MODEL).strip()
        if not api_key:
            raise GenerationConfigurationError("GEMINI_API_KEY is missing.")
        if not model:
            raise GenerationConfigurationError("GEMINI_GENERATION_MODEL must not be empty.")
        return cls(api_key=api_key, model=model)


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    sources: tuple[SearchResult, ...]


class GeminiAnswerProvider:
    """Use Gemini to answer only from numbered retrieval sources."""

    def __init__(self, settings: GenerationSettings, client: Any | None = None) -> None:
        self.settings = settings
        self._client = client or genai.Client(api_key=settings.api_key)

    def generate(self, query: str, sources: Sequence[SearchResult]) -> str:
        prompt = build_grounded_prompt(query, sources)
        try:
            response = self._client.models.generate_content(
                model=self.settings.model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0),
            )
        except Exception as exc:  # Provider exceptions vary by transport and status code.
            status_code = getattr(exc, "status_code", getattr(exc, "code", None))
            retryable = status_code == 429 or (isinstance(status_code, int) and status_code >= 500)
            raise GenerationRequestError(
                "Gemini answer generation failed.", retryable=retryable
            ) from exc

        answer = (response.text or "").strip()
        if not answer:
            raise GenerationRequestError("Gemini returned an empty answer.")
        return answer


class AnswerProvider(Protocol):
    def generate(self, query: str, sources: Sequence[SearchResult]) -> str: ...


def answer_with_retry(
    provider: AnswerProvider,
    query: str,
    sources: Sequence[SearchResult],
    *,
    max_attempts: int = 5,
    base_delay_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> AnswerResult:
    """Generate an answer, retrying only transient provider failures."""
    if not query.strip():
        raise ValueError("query must not be empty.")
    if not sources:
        return AnswerResult(
            answer="검색된 근거 문서가 없어 답변할 수 없습니다.",
            sources=(),
        )

    for attempt in range(1, max_attempts + 1):
        try:
            answer = provider.generate(query, sources)
            return AnswerResult(answer=answer, sources=tuple(sources))
        except GenerationRequestError as exc:
            if not exc.retryable or attempt == max_attempts:
                raise
            sleep(base_delay_seconds * (2 ** (attempt - 1)))

    raise AssertionError("Generation retry loop ended unexpectedly.")


def build_grounded_prompt(query: str, sources: Sequence[SearchResult]) -> str:
    """Build a prompt whose source numbers map directly to returned search results."""
    source_text = "\n\n".join(
        (
            f"[{index}] document_id={source.document_id}\n"
            f"title={source.title}\n"
            f"section={source.heading_path}\n"
            f"content:\n{source.content}"
        )
        for index, source in enumerate(sources, start=1)
    )
    return f"""다음 규칙을 지켜 질문에 답하세요.
- 제공된 근거만 사용하세요.
- 근거가 충분하지 않으면 "근거 문서에서 확인할 수 없습니다."라고 답하세요.
- 답변의 각 핵심 내용 뒤에 [1]처럼 근거 번호를 표시하세요.
- 간결한 한국어로 답하세요.

질문:
{query.strip()}

근거 문서:
{source_text}
"""
