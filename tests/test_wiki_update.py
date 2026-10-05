from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_wiki.answering import GenerationSettings
from llm_wiki.wiki_build import WikiBuilder, WikiBuildError, digest
from llm_wiki.wiki_content import WikiValidationError
from llm_wiki.wiki_query import WikiReader
from llm_wiki.wiki_update import WikiUpdatePlan, update_wiki
from llm_wiki.wiki_validation import verify_wiki

SETTINGS = GenerationSettings(api_key="test-key", model="test-model")


def write_source(root, relative, document_id, topic, body, **metadata):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = {"id": document_id, "title": document_id, "topic": topic, **metadata}
    path.write_text("---\n" + "\n".join(f"{k}: {v}" for k, v in fields.items())
                    + "\n---\n\n" + body + "\n", encoding="utf-8")
    return path


class GroundedModel:
    """Deterministic model double; tests exercise real planning, validation and publication."""

    def __init__(self):
        self.requests = []
        self.fail = False
        self.on_call = None

    def generate_content(self, **kwargs):
        prompt = kwargs["contents"]
        self.requests.append(prompt)
        if self.fail:
            raise ConnectionError("simulated provider outage")
        if self.on_call:
            callback, self.on_call = self.on_call, None
            callback()

        def after(marker):
            return json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])[0]

        def evidence(key, quote):
            return {"document_id": key, "quote": quote}

        if "이번에 작성할 문서 ID" in prompt:
            corpus = {s["id"]: s for s in after("대상 및 명시적 참조로 연결된 원본:\n")}
            ids = after("이번에 작성할 문서 ID(모두 정확히 한 번):\n")
            pages = []
            for key in ids:
                lines = [line for line in corpus[key]["body"].splitlines()
                         if line.strip() and not line.startswith(("#", "**Q."))]
                statements = [{"text": line, "evidence": [evidence(key, line)]} for line in lines]
                for other, source in corpus.items():
                    if source["metadata"]["topic"] == "incidents" and other != key:
                        quote = next(line for line in source["body"].splitlines()
                                     if line.startswith("- 원인:"))
                        statements.append({"text": quote, "evidence": [evidence(other, quote)]})
                pages.append({"document_id": key, "summary": statements[0],
                              "sections": [{"heading": "원본 설명", "statements": statements}]})
            result = {"pages": pages}
        elif "전체 카탈로그:\n" in prompt:
            catalog, ids = after("전체 카탈로그:\n"), after("대상 ID:\n")
            result = {"pages": [{"document_id": key, "related": [
                {"document_id": row["id"], "reason": "관련 문서"}
                for row in catalog if row["id"] != key][:8]} for key in ids]}
        elif "원본 ID·버전·본문:\n" in prompt:
            corpus = after("원본 ID·버전·본문:\n")
            findings = []
            faq = next((s for s in corpus if s["id"] == "faq-q02"), None)
            guides = [s for s in corpus if s["metadata"]["topic"] == "deploy-guide"]
            if faq and "배포를 허용한다" in faq["body"] and guides:
                latest = max(guides, key=lambda s: int(s["metadata"]["version"]))
                quote = next(line for line in latest["body"].splitlines()
                             if line.startswith("- 배포 금지 시간:"))
                findings.append({"rule": "배포 금지 시간", "description": "적용 시점 확인 필요",
                                 "evidence": [evidence(latest["id"], quote),
                                              evidence(faq["id"], "목요일 오후 배포를 허용한다.")]})
            result = {"reviewed_ids": [s["id"] for s in corpus], "findings": findings}
        else:
            rows = json.JSONDecoder().raw_decode(prompt.split("\n", 1)[1])[0]
            result = {"description": "문서 안내", "groups": [{"heading": "목록",
                                                          "ids": [row["id"] for row in rows]}]}
        return SimpleNamespace(text=json.dumps(result, ensure_ascii=False), usage_metadata=None)


@pytest.fixture
def built(tmp_path):
    sources, wiki = tmp_path / "sources", tmp_path / "wiki"
    write_source(sources, "deploy-guide/v30.md", "deploy-guide-v30", "deploy-guide",
                 "# 가이드\n- 변경: 현재 규칙 정리\n- 이유: 장애 리포트 #18 이후 결정\n"
                 "## 현재 규칙\n- 배포 금지 시간: 목요일 오후, 공휴일 전날", version=30)
    write_source(sources, "incidents/18.md", "incident-18", "incidents",
                 "# 장애\n- 원인: 정산 배치와 배포가 겹쳤다.\n- 조치: 배포 시기를 변경했다.", number=18)
    write_source(sources, "onboarding-faq/Q02.md", "faq-q02", "onboarding-faq",
                 "# FAQ\n목요일 오후에는 배포하지 않는다.\n- 관련 문서: 배포 가이드 v30", number=2)
    write_source(sources, "error-codes/E-001.md", "error-E-001", "error-codes",
                 "# 오류\n- 원인: 인증 토큰이 만료되었다.")
    model = GroundedModel()
    WikiBuilder(sources, wiki, SETTINGS, batch_size=1, interval=0,
                client=SimpleNamespace(models=model), progress=lambda *a, **k: None).build()
    assert verify_wiki(sources, wiki)["status"] == "PASS"
    model.requests.clear()
    return sources, wiki, model


def run_update(built, **kwargs):
    sources, wiki, model = built
    return update_wiki(sources, wiki, settings=SETTINGS, interval=0,
                       client=SimpleNamespace(models=model), progress=lambda *a, **k: None, **kwargs)


def add_v31(sources):
    return write_source(sources, "deploy-guide/v31.md", "deploy-guide-v31", "deploy-guide",
                        "# 가이드\n- 변경: 연말 동결 추가\n## 현재 규칙\n"
                        "- 배포 금지 시간: 목요일 오후, 공휴일 전날, 연말 동결\n"
                        "- 이전 버전: 배포 가이드 v30", version=31)


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_no_change_and_dry_run_do_not_call_model_or_write(built):
    sources, wiki, model = built
    before = snapshot(wiki)
    assert update_wiki(sources, wiki)["status"] == "unchanged"
    add_v31(sources)
    report = run_update(built, dry_run=True)
    assert report["added_sources"] == ["deploy-guide-v31"]
    assert "error-E-001" in report["reuse_pages"]
    assert snapshot(wiki) == before and not model.requests


def test_add_version_updates_history_latest_links_and_retains_unrelated_draft(built):
    sources, wiki, model = built
    add_v31(sources)
    report = run_update(built)
    assert report["status"] == "updated"
    assert "error-E-001" in report["reuse_pages"]
    assert not any('"error-E-001"' in p.split("이번에 작성할 문서 ID", 1)[-1]
                   for p in model.requests if "이번에 작성할 문서 ID" in p)
    text = (wiki / "deployments/v31.md").read_text()
    assert "v30 → v31" in text and "연말 동결" in text and "구축 원본 기준 최신" in text
    old = (wiki / "deployments/v30.md").read_text()
    assert "v31.md" in old and "과거 버전" in old and "v30 → v31" not in old
    assert "v31.md" in (wiki / "deployments/index.md").read_text()
    assert Path(report["backup"]).is_dir()
    state = json.loads((wiki / "_build/manifest.json").read_text())
    assert state["updates"][-1]["source_changes"]["deploy-guide-v31"]["before"] is None
    reader = WikiReader(wiki, sources)
    assert reader.originals("deployments/v31.md", reader.read("deployments/v31.md", "test"))
    model.requests.clear()
    assert run_update(built)["status"] == "unchanged" and not model.requests


def test_modified_incident_refreshes_dependent_pages(built):
    sources, wiki, _ = built
    path = sources / "incidents/18.md"
    path.write_text(path.read_text().replace("정산 배치와 배포가 겹쳤다.", "정산 잠금이 해제되지 않았다."))
    plan = WikiUpdatePlan(sources, wiki).report()
    assert "deploy-guide-v30" in plan["regenerate_pages"]
    assert "error-E-001" in plan["reuse_pages"]
    run_update(built)
    assert "정산 잠금이 해제되지 않았다" in (wiki / "deployments/v30.md").read_text()
    assert verify_wiki(sources, wiki)["status"] == "PASS"


def test_deleted_referenced_source_removes_page_links_and_evidence(built):
    sources, wiki, _ = built
    (sources / "incidents/18.md").unlink()
    report = run_update(built)
    assert report["deleted_sources"] == ["incident-18"]
    assert "incidents/18.md" in report["deleted_files"]
    assert not (wiki / "incidents").exists()
    assert "../incidents/18.md" not in (wiki / "deployments/v30.md").read_text()
    assert verify_wiki(sources, wiki)["status"] == "PASS"


def test_removed_latest_falls_back_and_history_is_recomputed(built):
    sources, wiki, _ = built
    path = add_v31(sources)
    run_update(built)
    path.unlink()
    report = run_update(built)
    assert "deployments/v31.md" in report["deleted_files"]
    assert "구축 원본 기준 최신" in (wiki / "deployments/v30.md").read_text()
    assert "v31.md" not in (wiki / "deployments/index.md").read_text()
    assert verify_wiki(sources, wiki)["rule_changes"] == 0


def test_conflict_appears_and_disappears_after_source_correction(built):
    sources, wiki, _ = built
    faq = sources / "onboarding-faq/Q02.md"
    original = faq.read_text()
    faq.write_text(original.replace("목요일 오후에는 배포하지 않는다.", "목요일 오후 배포를 허용한다."))
    run_update(built)
    assert "## 확인 필요" in (wiki / "deployments/v30.md").read_text()
    assert "## 확인 필요" in (wiki / "onboarding/Q02.md").read_text()
    faq.write_text(original)
    run_update(built)
    assert "## 확인 필요" not in (wiki / "deployments/v30.md").read_text()
    assert verify_wiki(sources, wiki)["conflict_candidates"] == 0


def test_failure_keeps_published_wiki_and_retains_diagnostics(built):
    sources, wiki, model = built
    before = snapshot(wiki)
    add_v31(sources)
    model.fail = True
    with pytest.raises(WikiBuildError):
        run_update(built)
    assert snapshot(wiki) == before
    assert list(wiki.parent.glob(".wiki.update-*/_build/manifest.json"))
    assert not (wiki.parent / ".wiki.update.lock").exists()


def test_sources_changing_during_generation_cannot_publish(built):
    sources, wiki, model = built
    before = snapshot(wiki)
    path = add_v31(sources)
    model.on_call = lambda: path.write_text(path.read_text() + "\n- 추가 규칙: 승인 필요\n")
    with pytest.raises(WikiValidationError, match="snapshot changed"):
        run_update(built)
    assert snapshot(wiki) == before


def test_edited_wiki_is_not_overwritten(built):
    sources, wiki, _ = built
    page = wiki / "deployments/v30.md"
    page.write_text(page.read_text() + "\n사용자의 수정\n")
    with pytest.raises(WikiValidationError, match="edited outside"):
        update_wiki(sources, wiki, dry_run=True)


def test_lf_crlf_only_is_unchanged(built):
    sources, _, model = built
    for path in sources.rglob("*.md"):
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert run_update(built)["status"] == "unchanged"
    assert not model.requests


def test_source_move_updates_original_links(built):
    sources, wiki, _ = built
    old = sources / "error-codes/E-001.md"
    old.rename(old.with_name("renamed.md"))
    report = run_update(built)
    assert report["modified_sources"] == ["error-E-001"]
    assert "renamed.md#L" in (wiki / "errors/E-001.md").read_text()
    assert "review-deployments" in report["reused_jobs"]
    assert verify_wiki(sources, wiki)["status"] == "PASS"


def test_legacy_build_dependencies_are_restored_from_recorded_prompts(built):
    sources, wiki, _ = built
    manifest = wiki / "_build/manifest.json"
    state = json.loads(manifest.read_text())
    del state["page_inputs"]
    manifest.write_text(json.dumps(state))
    assert WikiUpdatePlan(sources, wiki).report()["status"] == "unchanged"
    path = sources / "incidents/18.md"
    path.write_text(path.read_text().replace("정산 배치와 배포가 겹쳤다.", "정산 잠금이 해제되지 않았다."))
    plan = WikiUpdatePlan(sources, wiki).report()
    assert "deploy-guide-v30" in plan["regenerate_pages"]
    assert "error-E-001" in plan["reuse_pages"]


def test_publication_failure_restores_previous_directory(built, monkeypatch):
    sources, wiki, _ = built
    before = snapshot(wiki)
    add_v31(sources)
    rename = Path.rename

    def fail_stage(path, target):
        if path.name.startswith(".wiki.update-") and Path(target) == wiki:
            raise OSError("simulated rename failure")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_stage)
    with pytest.raises(OSError, match="rename failure"):
        run_update(built)
    assert snapshot(wiki) == before


def test_existing_update_lock_blocks_second_writer(built):
    sources, wiki, _ = built
    add_v31(sources)
    lock = wiki.parent / ".wiki.update.lock"
    lock.touch()
    with pytest.raises(WikiValidationError, match="Another update"):
        run_update(built)
    assert lock.exists()


def test_legacy_global_latest_input_is_invalidated_when_latest_changes(built):
    sources, wiki, _ = built
    manifest = wiki / "_build/manifest.json"
    state = json.loads(manifest.read_text())
    del state["page_inputs"]
    for call in state["calls"]:
        if call["job"].startswith("pages-") and call["status"] == "ok":
            path = wiki / (call["artifact_prefix"] + ".prompt.txt")
            text = path.read_text() + "\n구축 기준 최신 배포 원본 ID: deploy-guide-v30"
            path.write_text(text)
            call["prompt_sha256"] = digest(text.encode())
    manifest.write_text(json.dumps(state))
    add_v31(sources)
    report = run_update(built)
    assert "legacy_global_latest_changed" in report["reasons"]["error-E-001"]
    # The regenerated pages no longer depend on the global latest version.
    (sources / "deploy-guide/v31.md").unlink()
    assert "error-E-001" in WikiUpdatePlan(sources, wiki).report()["reuse_pages"]


def test_unrelated_unchanged_files_keep_modification_time(built):
    sources, wiki, _ = built
    page = wiki / "deployments/v30.md"
    os.utime(page, (1_000_000, 1_000_000))
    original_stat, original_text = page.stat(), page.read_text()
    error = sources / "error-codes/E-001.md"
    error.write_text(error.read_text().replace("인증 토큰이 만료되었다.", "인증 토큰이 누락되었다."))
    report = run_update(built)
    assert "deployments/v30.md" in report["unchanged_files"]
    assert page.read_text() == original_text and page.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert "review-deployments" in report["reused_jobs"]


def test_newly_resolved_reference_invalidates_existing_draft(built):
    sources, wiki, _ = built
    faq = sources / "onboarding-faq/Q02.md"
    faq.write_text(faq.read_text() + "\n- 관련 문서: 장애 리포트 #19\n")
    run_update(built)
    write_source(sources, "incidents/19.md", "incident-19", "incidents",
                 "# 장애\n- 원인: 새 장애의 원인 설명이다.", number=19)
    plan = WikiUpdatePlan(sources, wiki).report()
    assert "reference_set_changed" in plan["reasons"]["faq-q02"]
    run_update(built)
    assert "새 장애의 원인 설명" in (wiki / "onboarding/Q02.md").read_text()


def test_model_change_refreshes_all_pages(built):
    sources, wiki, model = built
    report = update_wiki(sources, wiki, settings=GenerationSettings(api_key="test", model="new-model"),
                         interval=0, client=SimpleNamespace(models=model),
                         progress=lambda *a, **k: None)
    assert len(report["regenerate_pages"]) == 4 and not report["reuse_pages"]
    assert not report["reused_jobs"]
    assert json.loads((wiki / "_build/manifest.json").read_text())["settings"]["model"] == "new-model"


def test_changed_published_wiki_during_generation_is_preserved(built):
    sources, wiki, model = built
    add_v31(sources)
    path = wiki / "deployments/v30.md"
    model.on_call = lambda: path.write_text(path.read_text() + "\n편집 중인 내용\n")
    with pytest.raises(WikiValidationError, match="edited outside"):
        run_update(built)
    assert "편집 중인 내용" in path.read_text()
    assert not (wiki / "deployments/v31.md").exists()


def test_new_source_snapshot_does_not_reuse_old_retry_feedback(built):
    sources, wiki, model = built
    manifest = wiki / "_build/manifest.json"
    state = json.loads(manifest.read_text())
    state["calls"].append({"job": "links-deployments", "status": "invalid_output",
                           "error": "STALE_VALIDATION_ERROR_FROM_OLD_SOURCES"})
    manifest.write_text(json.dumps(state))
    add_v31(sources)
    run_update(built)
    assert all("STALE_VALIDATION_ERROR_FROM_OLD_SOURCES" not in prompt for prompt in model.requests)
    updated = json.loads(manifest.read_text())
    assert any(call.get("error") == "STALE_VALIDATION_ERROR_FROM_OLD_SOURCES"
               for call in updated["calls"])
