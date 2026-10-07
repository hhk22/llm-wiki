import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_wiki.answer_records import AnswerStore, EvaluationInput, FeedbackInput
from llm_wiki.evaluation_runs import (
    AnswerCase,
    attach_reviews,
    compare_answers,
    load_cases,
    source_snapshot,
    write_report,
)
from llm_wiki.update_evaluation import compare_updates

pytest_plugins = ["test_wiki_update"]


def test_same_history_and_budget_without_leaking_expected_answers():
    seen = []
    case = AnswerCase(id="followup", query="그 전에는?", expected="SECRET_EXPECTED",
                      required_document_ids=["SECRET_DOC"], expected_status="answered",
                      history=[{"role": "user", "content": "배포 시간은?"}])

    def send(request):
        seen.append(copy.deepcopy(request))
        assert "SECRET" not in json.dumps(request)
        return 200, {"status": "answered", "sources": [
            {"originals": [{"document_id": "SECRET_DOC"}]}], "trace": {"calls": []}}

    report = compare_answers([case], ["keyword", "wiki"], 2, send,
                             metadata={"max_input_tokens": 1200})
    assert len(seen) == 4
    assert all(r["history"] == seen[0]["history"] for r in seen)
    assert all(r["max_input_tokens"] == 1200 for r in seen)
    assert [r["method"] for r in seen] == ["keyword", "wiki", "wiki", "keyword"]
    assert report["status"] == "complete"
    assert report["results"][0]["checks"] == {
        "expected_status": True, "required_sources_present": True,
        "semantic_correctness": "unreviewed"}


def test_failures_remain_in_report_and_partial_progress_is_saved(tmp_path):
    path = tmp_path / "report.json"
    cases = [AnswerCase(id="a", query="질문"), AnswerCase(id="b", query="질문2")]
    calls = []

    def send(request):
        calls.append(request)
        if len(calls) == 2:
            raise ConnectionError("private")
        return 502, {"status": "error"}

    with pytest.raises(ConnectionError):
        compare_answers(cases, ["wiki"], 1, send, checkpoint=lambda r: write_report(path, r))
    report = json.loads(path.read_text())
    assert report["status"] == "failed"
    assert len(report["results"]) == 1
    assert "private" not in str(report)


def test_baseline_and_supplemental_are_separate_and_unchanged():
    root = Path(__file__).resolve().parents[1]
    baseline_path = root / "evaluation/questions.yaml"
    before = baseline_path.read_bytes()
    baseline = load_cases(baseline_path)
    extra = load_cases(root / "evaluation/answer-cases-v6.json")
    assert len(baseline) == 20
    assert len(extra) == 4
    report = compare_answers(baseline + extra, ["wiki"], 1,
                             lambda _: (200, {"status": "answered", "sources": []}))
    assert {s["group"]: s["runs"] for s in report["summary"]} == {
        "baseline20": 20, "supplemental": 4}
    assert baseline_path.read_bytes() == before


def test_feedback_cannot_become_a_regression_case_without_review(tmp_path):
    store = AnswerStore(tmp_path / "records.db")
    store.save("one", {"request": {"query": "원래 질문", "history": []}, "response": {}})
    store.add_feedback("one", FeedbackInput(rating="unhelpful", comment="내 말이 정답임"))
    with pytest.raises(ValueError, match="human evaluation"):
        store.regression_case("one")
    store.evaluate("one", EvaluationInput(reviewer="human", verdict="partial", evidence="mixed",
                                         reason="원문 확인", expected="검토된 기준"))
    path = tmp_path / "cases.json"
    write_report(path, {"cases": [store.regression_case("one")]})
    assert load_cases(path)[0].expected == "검토된 기준"


def test_review_comparisons_include_regression_and_ignore_unreviewed(tmp_path):
    store = AnswerStore(tmp_path / "records.db")
    results = []
    for key, method, verdict in [("one", "keyword", "correct"), ("two", "wiki", "incorrect")]:
        store.save(key, {"request": {"query": "질문"}, "response": {}})
        store.evaluate(key, EvaluationInput(reviewer="human", verdict=verdict,
                                            evidence="supported", reason="원문 비교",
                                            expected="동일한 정답 기준"))
        results.append({"group": "baseline20", "case_id": "a", "repeat": 1,
                        "method": method, "response": {"answer_id": key}})
    report = attach_reviews({"results": results}, store)
    assert report["reviewed_comparisons"][0]["result"] == "regressed"


def test_update_comparison_isolated_and_counts_only_new_calls(built, tmp_path):
    import shutil

    from test_wiki_update import SETTINGS, GroundedModel

    before, wiki, _ = built
    after = tmp_path / "changed"
    shutil.copytree(before, after)
    faq = after / "onboarding-faq/Q02.md"
    faq.write_text(faq.read_text().replace("목요일 오후에는 배포하지 않는다", "목요일 오후 배포를 허용한다"))
    old_hashes = source_snapshot(wiki)
    report = compare_updates(before, after, wiki, tmp_path / "comparison", SETTINGS,
                             interval=0, client_factory=lambda: SimpleNamespace(models=GroundedModel()),
                             progress=lambda *a, **kw: None)
    assert report["status"] == "complete"
    assert report["quality"] == "requires_human_review"
    assert {r["method"] for r in report["results"]} == {"incremental", "full"}
    assert all(r["verification"]["status"] == "PASS" for r in report["results"])
    assert all(r["model_calls"] > 0 for r in report["results"])
    assert all(r["usage"]["total_tokens"] is None for r in report["results"])
    assert source_snapshot(wiki) == old_hashes
    with pytest.raises(ValueError, match="new comparison"):
        compare_updates(before, after, wiki, tmp_path / "comparison", SETTINGS)


def test_update_comparison_failure_keeps_published_wiki(built, tmp_path):
    import shutil

    from test_wiki_update import SETTINGS, GroundedModel

    before, wiki, _ = built
    after = tmp_path / "changed"
    shutil.copytree(before, after)
    faq = after / "onboarding-faq/Q02.md"
    faq.write_text(faq.read_text() + "\n내용 추가\n")
    original = source_snapshot(wiki)
    model = GroundedModel()
    model.fail = True
    from llm_wiki.wiki_build import WikiBuildError

    with pytest.raises(WikiBuildError, match="request_error"):
        compare_updates(before, after, wiki, tmp_path / "comparison", SETTINGS,
                        interval=0, client_factory=lambda: SimpleNamespace(models=model),
                        progress=lambda *a, **kw: None)
    saved = json.loads((tmp_path / "comparison/report.json").read_text())
    assert saved["status"] == "failed"
    assert source_snapshot(wiki) == original
