from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from llm_wiki.answering import (
    GeminiAnswerProvider,
    GenerationConfigurationError,
    GenerationRequestError,
    GenerationSettings,
    answer_with_retry,
    build_grounded_prompt,
)
from llm_wiki.search import SearchResult


def source() -> SearchResult:
    return SearchResult(
        document_id="deploy-guide-v30",
        chunk_index=1,
        title="배포 가이드 v30",
        topic="deploy-guide",
        version="30",
        heading_path="배포 가이드 v30 > 현재 규칙",
        content="배포 금지 시간: 목요일 오후",
        score=0.9,
    )


class Models:
    def __init__(self, text: str) -> None:
        self.text = text
        self.kwargs: dict[str, Any] = {}

    def generate_content(self, **kwargs: Any) -> SimpleNamespace:
        self.kwargs = kwargs
        return SimpleNamespace(text=self.text)


class FlakyAnswerProvider:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def generate(self, query: str, sources: Any) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise GenerationRequestError("temporary", retryable=True)
        return "확인했습니다. [1]"


def test_generation_settings_load_model_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_GENERATION_MODEL", "test-model")

    settings = GenerationSettings.from_env()

    assert settings.api_key == "test-key"
    assert settings.model == "test-model"


def test_generation_settings_require_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(GenerationConfigurationError, match="GEMINI_API_KEY"):
        GenerationSettings.from_env()


def test_prompt_maps_numbered_source_to_document() -> None:
    prompt = build_grounded_prompt("언제 배포할 수 없나요?", [source()])

    assert "[1] document_id=deploy-guide-v30" in prompt
    assert "배포 금지 시간: 목요일 오후" in prompt
    assert "제공된 근거만 사용" in prompt


def test_answer_provider_returns_grounded_text() -> None:
    models = Models("목요일 오후에는 배포할 수 없습니다. [1]")
    client = SimpleNamespace(models=models)
    provider = GeminiAnswerProvider(
        GenerationSettings(api_key="test-key"),
        client=client,
    )

    answer = provider.generate("언제 배포할 수 없나요?", [source()])

    assert answer.endswith("[1]")
    assert "document_id=deploy-guide-v30" in models.kwargs["contents"]


def test_answer_without_sources_does_not_call_provider() -> None:
    models = Models("should not be used")
    provider = GeminiAnswerProvider(
        GenerationSettings(api_key="test-key"),
        client=SimpleNamespace(models=models),
    )

    result = answer_with_retry(provider, "없는 규칙은?", [])

    assert result.sources == ()
    assert "답변할 수 없습니다" in result.answer
    assert models.kwargs == {}


def test_answer_retries_transient_generation_failure() -> None:
    provider = FlakyAnswerProvider(failures=2)
    delays: list[float] = []

    result = answer_with_retry(
        provider,
        "질문",
        [source()],
        max_attempts=3,
        base_delay_seconds=1,
        sleep=delays.append,
    )

    assert result.answer == "확인했습니다. [1]"
    assert provider.calls == 3
    assert delays == [1, 2]
