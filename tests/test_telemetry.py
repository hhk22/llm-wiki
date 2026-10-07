from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_api import search_result

from llm_wiki import api
from llm_wiki.answering import GeminiAnswerProvider, GenerationSettings, answer_with_retry
from llm_wiki.conversation import GeminiJsonModel, QuestionResolution
from llm_wiki.embedding import EmbeddingSettings, GeminiEmbeddingProvider
from llm_wiki.telemetry import ACTIVE, InputBudgetExceeded, request_trace, usage_summary

SETTINGS = GenerationSettings("test-key", "test-model")


def sdk(tokens=600, text="답변 [1]"):
    return SimpleNamespace(models=SimpleNamespace(
        count_tokens=lambda **kw: SimpleNamespace(total_tokens=tokens),
        generate_content=lambda **kw: SimpleNamespace(text=text, usage_metadata=None),
    ))


def test_rag_and_resolution_share_cumulative_budget_at_api_boundary(monkeypatch):
    resolution = sdk(600, '{"status":"resolved","resolved_query":"배포 규칙?",'
                          '"clarification":""}')
    generator = sdk(600)
    generated = []
    generator.models.generate_content = lambda **kw: generated.append(kw)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(api.WikiService, "search", lambda *a, **kw: [search_result()])
    monkeypatch.setattr(api, "GeminiAnswerProvider",
                        lambda settings: GeminiAnswerProvider(SETTINGS, generator))
    client = TestClient(api.create_app(model_factory=lambda budget: GeminiJsonModel(
        SETTINGS, budget=budget, client=resolution)))
    result = client.post("/answer", json={
        "query": "그 전에는?", "method": "keyword", "max_input_tokens": 1000,
        "history": [{"role": "user", "content": "배포 금지 시간은?"}],
    }).json()
    assert result["status"] == "budget_exceeded"
    assert result["trace"]["input_text_tokens"] == 600
    assert generated == []
    assert result["answer_id"]
    assert result["trace"]["retrieved"][0]["document_id"] == "deploy-guide-v30"
    assert ACTIVE.get() is None


def test_retry_inputs_are_counted_and_unknown_usage_is_not_zero():
    class Transient(Exception):
        code = 429

    client = sdk(600)
    attempts = []

    def fail(**kwargs):
        attempts.append(kwargs)
        raise Transient()

    client.models.generate_content = fail
    provider = GeminiAnswerProvider(SETTINGS, client)
    with request_trace(1000) as trace:
        with pytest.raises(InputBudgetExceeded):
            answer_with_retry(provider, "질문", [search_result()], sleep=lambda _: None)
        assert trace.used == 600
        assert len(attempts) == 1
        summary = usage_summary(trace.calls)
        assert summary["missing_usage_calls"] == 1
        assert summary["total_tokens"] is None


def test_embedding_and_generation_have_separate_usage_and_stages():
    embedding_client = SimpleNamespace(models=SimpleNamespace(
        embed_content=lambda **kw: SimpleNamespace(
            embeddings=[SimpleNamespace(values=[0.1, 0.2])], usage_metadata=None)))
    with request_trace(1000) as trace:
        GeminiEmbeddingProvider(EmbeddingSettings("test", dimensions=2), embedding_client).embed_query(
            "질문")
        GeminiAnswerProvider(SETTINGS, sdk(200)).generate("질문", [search_result()])
        assert trace.used == 200
        assert [c["operation"] for c in trace.calls] == [
            "embed_content", "count_tokens", "generate_content"]
        assert trace.calls[0]["stage"] == "embedding"
        assert trace.calls[-1]["stage"] == "rag_answer"
        assert usage_summary(trace.calls)["total_tokens"] is None


def test_trace_context_resets_even_after_failure():
    with pytest.raises(RuntimeError), request_trace(1000):
        raise RuntimeError()
    assert ACTIVE.get() is None


def test_structured_and_plain_generation_usage_share_same_trace():
    structured = sdk(300, '{"status":"resolved","resolved_query":"질문","clarification":""}')
    with request_trace(1000) as trace:
        model = GeminiJsonModel(SETTINGS, client=structured)
        model.run("resolve_question", "prompt", QuestionResolution)
        GeminiAnswerProvider(SETTINGS, sdk(400)).generate("질문", [search_result()])
        assert trace.used == 700
        assert sum(c["operation"] == "generate_content" for c in trace.calls) == 2


def test_rag_provenance_uses_same_database_snapshot_as_retrieval(monkeypatch):
    from contextlib import nullcontext

    statements = []

    class Connection:
        def execute(self, sql, params=None):
            statements.append((sql, params))
            if "content_hash" in sql:
                return SimpleNamespace(fetchall=lambda: [("deploy-guide-v30", "a" * 64)])
            return None

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(api, "connect_database", lambda _: nullcontext(Connection()))
    monkeypatch.setattr(api, "keyword_search", lambda *a, **kw: [search_result()])
    monkeypatch.setattr(api, "follow_causal_reference", lambda c, q, results, **kw: results)
    with request_trace(1000) as trace:
        assert api.WikiService().search("배포 규칙", "keyword", 1) == [search_result()]
        assert trace.provenance["retrieved_document_hashes"] == {"deploy-guide-v30": "a" * 64}
        assert "REPEATABLE READ" in statements[0][0]
