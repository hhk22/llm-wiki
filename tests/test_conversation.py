from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from llm_wiki.answering import GenerationRequestError, GenerationSettings
from llm_wiki.conversation import (
    ConversationTurn,
    GeminiJsonModel,
    InputBudgetExceeded,
    QuestionResolution,
    resolve_question,
)


class ScriptedModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []
        self.calls = []

    def run(self, stage, prompt, schema):
        self.prompts.append((stage, prompt))
        self.calls.append({"stage": stage})
        return schema.model_validate(self.outputs.pop(0))


def test_history_is_only_used_to_resolve_question():
    model = ScriptedModel([{
        "status": "resolved", "resolved_query": "목요일 오후로 변경되기 전 배포 금지 시간은?",
        "clarification": "",
    }])
    history = [ConversationTurn(role="user", content="배포 금지 시간이 왜 바뀌었어?"),
               ConversationTurn(role="assistant", content="이전 답변의 미검증 주장")]
    result = resolve_question("그 전에는?", history, model)
    assert result.resolved_query == "목요일 오후로 변경되기 전 배포 금지 시간은?"
    assert "이전 답변은 검증된 사실이 아니다" in model.prompts[0][1]
    assert "이전 답변의 미검증 주장" in model.prompts[0][1]


def test_no_history_preserves_query_and_skips_model():
    assert resolve_question("E-009 대응은?", [], None).resolved_query == "E-009 대응은?"


@pytest.mark.parametrize("payload", [
    {"status": "resolved", "resolved_query": "", "clarification": ""},
    {"status": "clarification_required", "resolved_query": "추측", "clarification": "무엇?"},
    {"status": "clarification_required", "resolved_query": "", "clarification": ""},
])
def test_inconsistent_resolution_is_rejected(payload):
    with pytest.raises(ValidationError):
        QuestionResolution.model_validate(payload)


def test_prompt_budget_prevents_generation():
    calls = []
    models = SimpleNamespace(
        count_tokens=lambda **kw: SimpleNamespace(total_tokens=1001),
        generate_content=lambda **kw: calls.append(kw),
    )
    model = GeminiJsonModel(GenerationSettings("unused"), budget=1000,
                            client=SimpleNamespace(models=models))
    with pytest.raises(InputBudgetExceeded):
        model.run("test", "prompt", QuestionResolution)
    assert calls == []


def test_structured_model_tracks_usage_and_cumulative_input():
    models = SimpleNamespace(
        count_tokens=lambda **kw: SimpleNamespace(total_tokens=700),
        generate_content=lambda **kw: SimpleNamespace(
            text='{"status":"resolved","resolved_query":"질문","clarification":""}',
            usage_metadata=SimpleNamespace(model_dump=lambda **kw: {"total_token_count": 900}),
        ),
    )
    model = GeminiJsonModel(GenerationSettings("unused"), budget=1000,
                            client=SimpleNamespace(models=models))
    assert model.run("resolve_question", "prompt", QuestionResolution).resolved_query == "질문"
    assert model.calls[0]["usage"]["total_token_count"] == 900
    with pytest.raises(InputBudgetExceeded):
        model.run("next", "prompt", QuestionResolution)
    assert len(model.calls) == 1


def test_invalid_provider_response_is_not_used():
    models = SimpleNamespace(
        count_tokens=lambda **kw: SimpleNamespace(total_tokens=10),
        generate_content=lambda **kw: SimpleNamespace(text="not json"),
    )
    model = GeminiJsonModel(GenerationSettings("unused"), client=SimpleNamespace(models=models))
    with pytest.raises(GenerationRequestError):
        model.run("resolve_question", "prompt", QuestionResolution)
    assert model.calls[0]["status"] == "failed"
