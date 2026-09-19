from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm_wiki.embedding import (
    EmbeddingConfigurationError,
    EmbeddingRequestError,
    EmbeddingSettings,
    GeminiEmbeddingProvider,
)


class FakeModels:
    def __init__(self, values: list[float]) -> None:
        self.values = values
        self.calls: list[dict[str, object]] = []

    def embed_content(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=self.values)])


class FakeClient:
    def __init__(self, values: list[float]) -> None:
        self.models = FakeModels(values)


class FailingModels:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def embed_content(self, **kwargs: object) -> SimpleNamespace:
        error = RuntimeError("provider failure")
        error.code = self.status_code
        raise error


class FailingClient:
    def __init__(self, status_code: int) -> None:
        self.models = FailingModels(status_code)


def test_settings_require_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(EmbeddingConfigurationError):
        EmbeddingSettings.from_env()


def test_query_uses_retrieval_prefix_and_requested_dimensions() -> None:
    client = FakeClient([0.1, 0.2, 0.3])
    provider = GeminiEmbeddingProvider(
        EmbeddingSettings(api_key="test-key", dimensions=3),
        client=client,
    )

    vector = provider.embed_query(" 배포 금지 시간은? ")

    assert vector == [0.1, 0.2, 0.3]
    assert client.models.calls[0]["contents"] == "task: search result | query: 배포 금지 시간은?"
    config = client.models.calls[0]["config"]
    assert config.output_dimensionality == 3


def test_document_uses_title_and_text_format() -> None:
    client = FakeClient([0.1, 0.2, 0.3])
    provider = GeminiEmbeddingProvider(
        EmbeddingSettings(api_key="test-key", dimensions=3),
        client=client,
    )

    provider.embed_document("목요일에는 배포하지 않는다.", title="배포 가이드")

    assert (
        client.models.calls[0]["contents"]
        == "title: 배포 가이드 | text: 목요일에는 배포하지 않는다."
    )


def test_rejects_unexpected_vector_dimensions() -> None:
    provider = GeminiEmbeddingProvider(
        EmbeddingSettings(api_key="test-key", dimensions=3),
        client=FakeClient([0.1, 0.2]),
    )

    with pytest.raises(EmbeddingRequestError, match="Expected 3 dimensions"):
        provider.embed_query("배포")


@pytest.mark.parametrize(("status_code", "retryable"), [(429, True), (503, True), (400, False)])
def test_provider_errors_identify_retryable_statuses(
    status_code: int,
    retryable: bool,
) -> None:
    provider = GeminiEmbeddingProvider(
        EmbeddingSettings(api_key="test-key", dimensions=3),
        client=FailingClient(status_code),
    )

    with pytest.raises(EmbeddingRequestError) as error:
        provider.embed_query("배포")

    assert error.value.retryable is retryable
