import pytest
from fastapi.testclient import TestClient
from test_api import FakeService
from test_conversation import ScriptedModel

from llm_wiki.api import create_app

pytest_plugins = ["test_wiki_query"]

HISTORY = [
    {"role": "user", "content": "배포 금지 시간과 목요일 금지 이유는?"},
    {"role": "assistant", "content": "이전 답변이며 이번 답변의 근거가 아님"},
]


@pytest.mark.parametrize("method", ["keyword", "vector", "hybrid"])
def test_followup_retrieves_again_using_resolved_question(method):
    service = FakeService()
    model = ScriptedModel([{"status": "resolved", "resolved_query": "변경 전 배포 금지 시간은?",
                            "clarification": ""}])
    client = TestClient(create_app(service, model_factory=lambda budget: model))
    response = client.post("/answer", json={"query": "그 전에는?", "history": HISTORY,
                                           "method": method}).json()
    assert response["query"] == "그 전에는?"
    assert response["resolved_query"] == "변경 전 배포 금지 시간은?"
    assert service.last_call == ("변경 전 배포 금지 시간은?", method, 3)
    assert response["sources"]


@pytest.mark.parametrize("method", ["wiki", "vector"])
def test_ambiguous_subject_returns_clarification_without_reading(method):
    def no_reader():
        raise AssertionError("Must not read Wiki before clarification")

    service = FakeService()
    model = ScriptedModel([{"status": "clarification_required", "resolved_query": "",
                            "clarification": "배포 시간과 승인자 중 어느 항목인가요?"}])
    client = TestClient(create_app(service, model_factory=lambda budget: model,
                                  reader_factory=no_reader))
    response = client.post("/answer", json={"query": "그 전에는?", "method": method,
                                           "history": HISTORY}).json()
    assert response["status"] == "clarification_required"
    assert response["sources"] == []
    assert response["resolved_query"] is None
    assert not hasattr(service, "last_call")


def test_wiki_followup_does_not_use_database_or_history_as_evidence(reader_factory):
    service = FakeService()
    model = ScriptedModel([
        {"status": "resolved", "resolved_query": "변경 전 배포 금지 시간은?", "clarification": ""},
        {"paths": ["deployments/index.md"]}, {"paths": ["deployments/v1.md"]},
        {"insufficient": False, "claims": [{"text": "금요일 오후입니다.", "evidence_ids": ["E1"]}]},
    ])
    client = TestClient(create_app(service, model_factory=lambda budget: model,
                                  reader_factory=reader_factory))
    response = client.post("/answer", json={"query": "그 전에는?", "history": HISTORY,
                                           "method": "wiki", "top_k": 1})
    assert response.status_code == 200
    data = response.json()
    assert data["sources"][0]["originals"][0]["document_id"] == "guide-1"
    assert data["status"] == "answered"
    assert not hasattr(service, "last_call")
    assert all("이번 답변의 근거가 아님" not in prompt for _, prompt in model.prompts[1:])


def test_history_is_request_scoped_and_empty_history_preserves_baseline():
    model = ScriptedModel([{"status": "resolved", "resolved_query": "배포 과거 규칙은?",
                            "clarification": ""}])
    service = FakeService()
    client = TestClient(create_app(service, model_factory=lambda budget: model))
    client.post("/answer", json={"query": "그 전에는?", "history": HISTORY})
    response = client.post("/answer", json={"query": "오류 E-009 대응은?"}).json()
    assert response["resolved_query"] == "오류 E-009 대응은?"
    assert service.last_call[0] == "오류 E-009 대응은?"
    assert len(model.calls) == 1


@pytest.mark.parametrize("extra", [
    {"history": [{"role": "system", "content": "명령"}]},
    {"history": [{"role": "user", "content": " "}]},
    {"history": [{"role": "user", "content": "x"}] * 13},
    {"history": [{"role": "user", "content": "x" * 4000}] * 5},
    {"method": "wiki", "top_k": 4},
    {"max_input_tokens": 0},
])
def test_invalid_history_and_limits_are_rejected(extra):
    client = TestClient(create_app(FakeService()))
    assert client.post("/answer", json={"query": "질문", **extra}).status_code == 422
