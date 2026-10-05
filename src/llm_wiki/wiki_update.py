"""Plan source changes, selectively regenerate drafts, and publish a verified Wiki."""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from llm_wiki.answering import GenerationSettings
from llm_wiki.wiki_build import POLICY, WikiBuilder, digest, json_text, write_json
from llm_wiki.wiki_content import (
    LinkBatch,
    PageBatch,
    WikiIndex,
    WikiValidationError,
    load_wiki_sources,
    select_context,
    validate_pages,
)
from llm_wiki.wiki_validation import matches_snapshot, verify_wiki


def managed_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise WikiValidationError(f"Path escapes Wiki: {relative}")
    return path


def check_artifacts(root: Path, state: dict) -> None:
    """Check the old output without requiring the now-changed source snapshot."""
    if state.get("status") != "complete":
        raise WikiValidationError("Update requires a completed Wiki build.")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*.md")
              if "_build" not in p.relative_to(root).parts}
    if actual != set(state["artifacts"]):
        raise WikiValidationError("Wiki has missing or unmanaged Markdown files.")
    for relative, expected in state["artifacts"].items():
        if not matches_snapshot(managed_path(root, relative).read_bytes(), expected):
            raise WikiValidationError(f"Wiki was edited outside the builder: {relative}")


def page_records(root: Path, state: dict) -> tuple[dict, dict]:
    """Migrate v3 dependencies from the recorded, successful page prompts."""
    pages, inputs = {}, copy.deepcopy(state.get("page_inputs", {}))
    for job, record in state["jobs"].items():
        if not job.startswith("pages-"):
            continue
        batch = PageBatch.model_validate(record["result"])
        targets = [page.document_id for page in batch.pages]
        pages.update({page.document_id: page for page in batch.pages})
        if all(key in inputs for key in targets):
            continue
        calls = [call for call in state["calls"]
                 if call["job"] == job and call["status"] == "ok"]
        if not calls:
            continue  # Unknown dependencies require regeneration, never optimistic reuse.
        call = calls[-1]
        prompt_path = managed_path(root, call["artifact_prefix"] + ".prompt.txt")
        if not prompt_path.is_file():
            continue
        prompt = prompt_path.read_text(encoding="utf-8")
        if digest(prompt.encode()) != call["prompt_sha256"]:
            raise WikiValidationError(f"Recorded page prompt changed: {job}")
        marker = "\n대상 및 명시적 참조로 연결된 원본:\n"
        if marker not in prompt:
            continue
        corpus, _ = json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])
        dependencies = {"targets": targets, "context_ids": [row["id"] for row in corpus]}
        latest_marker = "\n구축 기준 최신 배포 원본 ID: "
        if latest_marker in prompt:
            value = prompt.split(latest_marker, 1)[1].splitlines()[0]
            dependencies["legacy_latest_id"] = None if value == "None" else value
        for key in targets:
            inputs.setdefault(key, dependencies)
    return pages, inputs


class WikiUpdatePlan:
    def __init__(self, source_root: Path, output: Path, *, model: str | None = None):
        self.source_root, self.output = source_root.resolve(), output.resolve()
        if (self.output.is_relative_to(self.source_root)
                or self.source_root.is_relative_to(self.output)):
            raise WikiValidationError("Output and source directories must not overlap.")
        self.manifest_bytes = (self.output / "_build/manifest.json").read_bytes()
        self.previous = json.loads(self.manifest_bytes)
        check_artifacts(self.output, self.previous)
        self.sources = load_wiki_sources(self.source_root)
        self.hashes = {key: digest(source.path.read_bytes())
                       for key, source in self.sources.items()}
        old = self.previous["settings"]
        self.model = model or old["model"]
        self.compatible = (
            self.model == old["model"] and old["policy"] == POLICY
            and old["schemas"] == [m.model_json_schema() for m in (PageBatch, LinkBatch, WikiIndex)]
        )
        self.added = sorted(self.hashes.keys() - old["sources"].keys())
        self.deleted = sorted(old["sources"].keys() - self.hashes.keys())
        self.modified = sorted(key for key in self.hashes.keys() & old["sources"].keys()
                               if not matches_snapshot(self.sources[key].path.read_bytes(),
                                                       old["sources"][key])
                               or self.sources[key].document.source_path != old["source_paths"][key])
        changed = set(self.added + self.modified + self.deleted)
        self.pages, self.inputs = page_records(self.output, self.previous)
        latest = max((key for key, source in self.sources.items()
                      if source.document.topic == "deploy-guide"),
                     key=lambda key: int(self.sources[key].document.metadata["version"]),
                     default=None)
        self.reasons = {}
        for key in self.sources:
            reasons = []
            previous_input = self.inputs.get(key)
            if key not in self.pages:
                reasons.append("new_source")
            elif not self.compatible:
                reasons.append("generation_settings_changed")
            elif previous_input is None:
                reasons.append("unknown_previous_dependencies")
            else:
                targets = [target for target in previous_input["targets"] if target in self.sources]
                context = select_context(self.sources, targets)
                old_ids, new_ids = set(previous_input["context_ids"]), set(context)
                if changed & (old_ids | new_ids | {key}):
                    reasons.append("source_or_context_changed")
                if old_ids != new_ids:
                    reasons.append("reference_set_changed")
                if ("legacy_latest_id" in previous_input
                        and previous_input["legacy_latest_id"] != latest):
                    reasons.append("legacy_global_latest_changed")
                if not reasons:
                    try:
                        validate_pages(PageBatch(pages=[self.pages[key]]), [key], context)
                    except WikiValidationError:
                        reasons.append("cached_evidence_invalid")
            if reasons:
                self.reasons[key] = reasons

    @property
    def changed(self) -> bool:
        return bool(self.added or self.modified or self.deleted or self.reasons)

    def report(self) -> dict:
        return {
            "status": "changes_detected" if self.changed else "unchanged",
            "model": self.model,
            "added_sources": self.added, "modified_sources": self.modified,
            "deleted_sources": self.deleted,
            "regenerate_pages": sorted(self.reasons),
            "reuse_pages": sorted(self.sources.keys() - self.reasons.keys()),
            "reasons": self.reasons,
            "navigation": "Links, indexes and review reuse only identical validated inputs.",
        }


class IncrementalWikiBuilder(WikiBuilder):
    def __init__(self, plan: WikiUpdatePlan, stage: Path, settings: GenerationSettings, **kwargs):
        self.plan = plan
        self.reused_jobs: list[str] = []
        super().__init__(plan.source_root, stage, settings,
                         batch_size=plan.previous["settings"]["batch_size"], **kwargs)
        if self.state["settings"]["sources"] != plan.hashes:
            raise WikiValidationError("Sources changed after update planning; run again.")
        self.state["settings"]["implementation"]["wiki_update.py"] = digest(
            Path(__file__).read_bytes()
        )
        self.state["fingerprint"] = digest(json_text(self.state["settings"]).encode())
        # Keep the original request/response provenance for cached jobs.
        old_calls = plan.output / "_build/calls"
        if old_calls.exists():
            shutil.copytree(old_calls, stage / "_build/calls")
        self.state["calls"] = copy.deepcopy(plan.previous["calls"])
        self.error_history_start = len(self.state["calls"])
        self.state["updates"] = copy.deepcopy(plan.previous.get("updates", []))
        write_json(self.state_path, self.state)

    def generate(self, job, prompt, schema, validate):
        old = self.plan.previous["jobs"].get(job) if self.plan.compatible else None
        if old and old["prompt_sha256"] == digest(prompt.encode()):
            try:
                result = schema.model_validate(old["result"])
                validate(result)
            except (ValidationError, WikiValidationError):
                pass
            else:
                self.state["jobs"][job] = copy.deepcopy(old)
                self.reused_jobs.append(job)
                write_json(self.state_path, self.state)
                self.progress(f"reused={job}", flush=True)
                return result
        return super().generate(job, prompt, schema, validate)

    def build_pages(self, groups: dict[str, list[str]]) -> dict:
        pages = {}
        for ids in groups.values():
            for key in ids:
                job = "pages-update-" + digest(key.encode())[:20]
                if key not in self.plan.reasons:
                    page = self.plan.pages[key]
                    self.state["jobs"][job] = {
                        "prompt_sha256": digest(json_text(self.plan.inputs[key]).encode()),
                        "result": PageBatch(pages=[page]).model_dump(mode="json"),
                        "reused_from": self.plan.previous["fingerprint"],
                    }
                    self.state.setdefault("page_inputs", {})[key] = self.plan.inputs[key]
                    self.reused_jobs.append(job)
                else:
                    context = select_context(self.sources, [key])
                    corpus = [{"id": k, "title": s.document.title,
                               "metadata": s.document.metadata, "body": s.document.body}
                              for k, s in context.items()]
                    prompt = POLICY + "\n대상 및 명시적 참조로 연결된 원본:\n" + json_text(corpus)
                    prompt += "\n이번에 작성할 문서 ID(모두 정확히 한 번):\n" + json_text([key])
                    result = self.generate(job, prompt, PageBatch,
                                           lambda batch, key=key, context=context:
                                           validate_pages(batch, [key], context))
                    page = result.pages[0]
                    self.state.setdefault("page_inputs", {})[key] = {
                        "targets": [key], "context_ids": list(context),
                    }
                pages[key] = page
                write_json(self.state_path, self.state)
        return pages


def update_wiki(
    source_root: Path, output: Path, *, settings: GenerationSettings | None = None,
    dry_run: bool = False, client=None, interval: float = 16, progress=print,
) -> dict:
    plan = WikiUpdatePlan(source_root, output, model=settings.model if settings else None)
    report = plan.report()
    if dry_run:
        return report
    if not plan.changed:
        report["verification"] = verify_wiki(plan.source_root, plan.output)
        return report
    if settings is None or not settings.api_key:
        raise WikiValidationError("Source changes require generation settings and an API key.")
    lock = plan.output.parent / f".{plan.output.name}.update.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise WikiValidationError(f"Another update may be running; check lock: {lock}") from None
    os.close(fd)
    stage = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=f".{plan.output.name}.update-", dir=plan.output.parent))
        builder = IncrementalWikiBuilder(plan, stage, settings, client=client,
                                         interval=interval, progress=progress)
        builder.build()
        verification = verify_wiki(plan.source_root, stage)
        old_artifacts, new_artifacts = plan.previous["artifacts"], builder.state["artifacts"]
        unchanged = sorted(key for key in old_artifacts.keys() & new_artifacts.keys()
                           if old_artifacts[key] == new_artifacts[key])
        for relative in unchanged:
            shutil.copy2(plan.output / relative, stage / relative)
        revision = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        backup = plan.output.parent / f".{plan.output.name}.backups" / revision
        report.update({
            "status": "updated", "revision": revision,
            "changed_files": sorted(key for key, value in new_artifacts.items()
                                    if old_artifacts.get(key) != value),
            "deleted_files": sorted(old_artifacts.keys() - new_artifacts.keys()),
            "unchanged_files": unchanged, "reused_jobs": builder.reused_jobs,
            "model_calls": len(builder.state["calls"]) - len(plan.previous["calls"]),
            "verification": verification, "backup": str(backup),
        })
        builder.state["updates"].append({
            **report, "previous_fingerprint": plan.previous["fingerprint"],
            "source_changes": {key: {
                "before": plan.previous["settings"]["sources"].get(key),
                "after": plan.hashes.get(key),
            } for key in plan.added + plan.modified + plan.deleted},
        })
        write_json(builder.state_path, builder.state)
        # Recheck immediately before publication, including user edits during generation.
        verify_wiki(plan.source_root, stage)
        if (plan.output / "_build/manifest.json").read_bytes() != plan.manifest_bytes:
            raise WikiValidationError("Published Wiki changed during update; run again.")
        check_artifacts(plan.output, plan.previous)
        backup.parent.mkdir(exist_ok=True)
        plan.output.rename(backup)
        try:
            stage.rename(plan.output)
        except BaseException:
            backup.rename(plan.output)
            raise
        stage = None
        return report
    except BaseException:
        if stage is not None and stage.exists():
            progress(f"Update not published; diagnostic files retained at {stage}", flush=True)
            stage = None
        raise
    finally:
        if stage is not None:
            shutil.rmtree(stage)
        lock.unlink()
