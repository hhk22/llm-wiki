from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_api import FakeService

from llm_wiki.answer_records import AnswerStore
from llm_wiki.api import create_app

EVALUATION = {"reviewer": "reviewer-1", "verdict": "incorrect", "evidence": "unsupported",
              "reason": "과거와 현재를 혼동", "expected": "목요일 오후 금지를 근거로 답한다.",
              "expected_status": "answered"}


def test_durable_feedback_evaluation_and_regression_export(tmp_path):
    store = AnswerStore(tmp_path / "answers.sqlite3")
    client = TestClient(create_app(FakeService(), record_store=store))
    request = {"query": "배포 금지 시간?", "method": "keyword"}
    response = client.post("/answer", json=request).json()
    key = response["answer_id"]
    assert key
    # A new store/app can read the same records; state is not held in memory.
    other = TestClient(create_app(FakeService(), record_store=AnswerStore(store.path)))
    record = other.get(f"/answers/{key}").json()
    assert record["request"]["query"] == request["query"]
    assert record["response"] == response
    assert record["code_sha256"]["api.py"]
    assert other.post(f"/answers/{key}/feedback", json={
        "rating": "unhelpful", "category": "answer", "comment": "시간이 다름",
    }).status_code == 201
    assert other.get(f"/answers/{key}/regression-case").status_code == 409
    assert other.post(f"/answers/{key}/evaluation", json=EVALUATION).status_code == 201
    case = other.get(f"/answers/{key}/regression-case").json()
    assert case["expected"] == EVALUATION["expected"]
    assert case["query"] == request["query"]
    assert case["origin_answer_id"] == key
    assert other.get(f"/answers/{key}").json()["response"] == response
    assert other.post(f"/answers/{uuid4().hex}/feedback", json={
        "rating": "helpful"}).status_code == 404
    assert other.post(f"/answers/{key}/feedback", json={
        "rating": "unhelpful", "comment": " "}).status_code == 422


def test_failed_answers_are_recorded_without_raw_exception_secrets(tmp_path):
    class Broken(FakeService):
        def answer(self, *args):
            raise RuntimeError("secret-api-key")

    store = AnswerStore(tmp_path / "records.db")
    client = TestClient(create_app(Broken(), record_store=store))
    response = client.post("/answer", json={"query": "질문"})
    assert response.status_code == 502
    key = response.json()["detail"]["answer_id"]
    record = store.get(key)
    assert record["response"]["status"] == "error"
    assert record["response"]["error_type"] == "RuntimeError"
    assert "secret-api-key" not in str(record)


def test_concurrent_requests_have_isolated_records_and_trace(tmp_path):
    client = TestClient(create_app(FakeService(), record_store=AnswerStore(tmp_path / "records.db")))

    def run(i):
        result = client.post("/answer", json={"query": f"질문 {i}"}).json()
        record = client.get(f"/answers/{result['answer_id']}").json()
        assert record["request"]["query"] == f"질문 {i}"
        assert record["response"]["trace"]["calls"] == []
        return result["answer_id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(run, range(12)))
    assert len(set(ids)) == 12


@pytest.mark.parametrize("field", ["reviewer", "expected", "reason"])
def test_human_evaluation_rejects_blank_fields(field):
    from pydantic import ValidationError

    from llm_wiki.answer_records import EvaluationInput

    with pytest.raises(ValidationError):
        EvaluationInput.model_validate({**EVALUATION, field: " "})
