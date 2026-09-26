from dataclasses import replace
from pathlib import Path

import pytest

from llm_wiki.wiki_content import (
    Evidence,
    PageLinks,
    Section,
    Statement,
    WikiPage,
    WikiValidationError,
    load_wiki_sources,
    render_page,
)
from llm_wiki.wiki_history import (
    ConflictFinding,
    TemporalReview,
    changes_for,
    comparable_rule,
    conflicting_values,
    deployment_history,
    review_sources,
    validate_review,
)


@pytest.fixture
def sources():
    return load_wiki_sources(Path(__file__).resolve().parents[1] / "sources")


def draft(source):
    evidence = Evidence(document_id=source.document.document_id,
                        quote=next(line for line in source.document.body.splitlines()
                                   if line.startswith("- 변경:")))
    statement = Statement(text="현재 규칙을 정리했다.", evidence=[evidence])
    return WikiPage(document_id=source.document.document_id, summary=statement,
                    sections=[Section(heading="현재 규칙", statements=[statement])])


def test_rule_history_contains_source_delta_and_only_explicit_causal_incident(sources):
    history = deployment_history(sources)
    ban = [h for h in history if h.rule == "배포 금지 시간"]
    assert [h.after.document_id for h in ban] == [
        "deploy-guide-v05", "deploy-guide-v16", "deploy-guide-v22", "deploy-guide-v25",
    ]
    v22 = next(h for h in ban if h.after.document_id == "deploy-guide-v22")
    assert v22.before.document_id == "deploy-guide-v21"
    assert "금요일" in v22.before.quote and "목요일" in v22.after.quote
    assert v22.incident_ids == ("incident-18",)
    v25 = next(h for h in ban if h.after.document_id == "deploy-guide-v25")
    assert "12/22~1/2" in v25.after.quote
    assert not v25.incident_ids and v25.reason is None


def test_missing_predecessor_cannot_invent_a_change_version(sources):
    partial = {key: sources[key] for key in ("deploy-guide-v22", "deploy-guide-v30")}
    assert deployment_history(partial) == []


def test_formatting_is_not_a_policy_change_but_code_and_word_boundaries_are_preserved(sources):
    assert comparable_rule("이전 빌드를 `deploy-prod`로 재실행") == comparable_rule("이전 빌드를`deploy-prod`로 재실행")
    assert comparable_rule("`deploy run`") != comparable_rule("`deploy  run`")
    assert comparable_rule("no where") != comparable_rule("nowhere")
    assert not any(h.rule == "롤백 명령" and h.after.document_id in {"deploy-guide-v03", "deploy-guide-v04"}
                   for h in deployment_history(sources))


def test_historical_page_excludes_future_history_and_qualifies_current(sources, tmp_path):
    key = "deploy-guide-v22"
    history = deployment_history(sources)
    assert all(int(sources[h.after.document_id].document.metadata["version"]) <= 22
               for h in changes_for(sources[key], history, sources))
    text = render_page(draft(sources[key]), PageLinks(document_id=key, related=[]),
                       sources, tmp_path / "wiki", "deploy-guide-v30", history,
                       TemporalReview(reviewed_ids=list(review_sources(sources)), findings=[]))
    assert "## v22 당시 규칙" in text and "## 현재 규칙" not in text
    assert "v22 당시 규칙을 정리했다." in text
    assert "[배포 가이드 v30](v30.md)" in text
    assert "v25 변경 문서" not in text and "12/22" not in text


def test_latest_page_links_to_change_versions_incident_and_original_lines(sources, tmp_path):
    key = "deploy-guide-v30"
    text = render_page(draft(sources[key]), PageLinks(document_id=key, related=[]),
                       sources, tmp_path / "wiki", key, deployment_history(sources),
                       TemporalReview(reviewed_ids=list(review_sources(sources)), findings=[]))
    assert "## 현재 규칙" in text
    assert "[v22 변경 문서](v22.md)" in text
    assert "[v25 변경 문서](v25.md)" in text
    assert "[원인 사건: 장애 리포트 #18](../incidents/18.md)" in text
    assert "sources/deploy-guide/v21.md#L" in text
    assert "sources/deploy-guide/v22.md#L" in text
    assert "확인 필요" not in text


def test_normal_version_change_is_rejected_as_conflict(sources):
    picked = review_sources(sources)
    h = next(h for h in deployment_history(sources)
             if h.rule == "배포 금지 시간" and h.after.document_id == "deploy-guide-v22")
    result = TemporalReview(reviewed_ids=list(picked), findings=[ConflictFinding(
        rule=h.rule, description="금요일과 목요일이 다르다.", evidence=[h.before, h.after],
    )])
    with pytest.raises(WikiValidationError, match="Different guide versions"):
        validate_review(result, picked)


def test_conflict_needs_real_quotes_from_distinct_sources(sources):
    picked = review_sources(sources)
    e = Evidence(document_id="faq-q02", quote="존재하지 않는 상충 근거")
    other = Evidence(document_id="deploy-guide-v30", quote="- 배포 금지 시간: 목요일 오후, 공휴일 전날, 12/22~1/2 연말 동결")
    review = TemporalReview(reviewed_ids=list(picked), findings=[ConflictFinding(
        rule="배포 금지 시간", description="시점 확인이 필요하다.", evidence=[e, other],
    )])
    with pytest.raises(WikiValidationError, match="not verbatim"):
        validate_review(review, picked)
    review.findings[0].evidence = [other, other]
    with pytest.raises(WikiValidationError, match="two different sources"):
        validate_review(review, picked)


def test_unclear_scope_conflict_preserves_both_claims_without_overriding_rules(sources, tmp_path):
    # Controlled fixture: keep published synthetic originals untouched.
    source = sources["faq-q02"]
    old = "목요일 오후, 공휴일 전날, 12월 22일부터 1월 2일까지는 배포하지 않는다."
    new = "현재 목요일 오후 배포를 허용한다."
    sources["faq-q02"] = replace(source, document=replace(
        source.document, body=source.document.body.replace(old, new)), raw=source.raw.replace(old, new))
    picked = review_sources(sources)
    review = TemporalReview(reviewed_ids=list(picked), findings=[ConflictFinding(
        rule="배포 금지 시간", description="FAQ는 목요일 배포를 허용하지만 가이드는 금지한다. FAQ의 적용 시점 확인이 필요하다.",
        evidence=[
            Evidence(document_id="faq-q02", quote=new),
            Evidence(document_id="deploy-guide-v30", quote="- 배포 금지 시간: 목요일 오후, 공휴일 전날, 12/22~1/2 연말 동결"),
        ],
    )])
    validate_review(review, picked)
    for key in ("deploy-guide-v30", "faq-q02"):
        page = (draft(sources[key]) if key.startswith("deploy") else WikiPage(
            document_id=key, summary=Statement(text=new, evidence=[review.findings[0].evidence[0]]),
            sections=[Section(heading="안내", statements=[Statement(text=new, evidence=[review.findings[0].evidence[0]])])],
        ))
        text = render_page(page, PageLinks(document_id=key, related=[]), sources, tmp_path / "wiki",
                           "deploy-guide-v30", deployment_history(sources), review)
        assert "## 확인 필요" in text
        assert new in text and "배포 금지 시간: 목요일 오후" in text
        assert "적용 버전 확인 필요" in text
        assert "sources/onboarding-faq/Q02.md#L13-L13" in text


def test_duplicate_rule_in_same_version_is_flagged_not_used_as_history(sources):
    source = sources["deploy-guide-v22"]
    body = source.document.body + "\n- 배포 금지 시간: 없음"
    sources["deploy-guide-v22"] = replace(source, document=replace(source.document, body=body))
    findings = conflicting_values(sources)
    assert len(findings) == 1 and findings[0].rule == "배포 금지 시간"
    assert len(findings[0].evidence) == 2
    assert not any(h.rule == "배포 금지 시간" and h.after.document_id == "deploy-guide-v22"
                   for h in deployment_history(sources))
