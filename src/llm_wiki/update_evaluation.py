"""Run full and incremental builds on isolated copies of the same source change."""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

from llm_wiki.evaluation_runs import source_snapshot, write_report
from llm_wiki.telemetry import usage_summary
from llm_wiki.wiki_build import WikiBuilder
from llm_wiki.wiki_update import update_wiki
from llm_wiki.wiki_validation import verify_wiki


def compare_updates(before: Path, after: Path, wiki: Path, output: Path, settings, *,
                    interval=16, client_factory=lambda: None, progress=print):
    before, after, wiki, output = (p.resolve() for p in (before, after, wiki, output))
    for source in (before, after, wiki):
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError("Comparison output must not overlap inputs")
    if output.exists():
        raise ValueError("Choose a new comparison output directory")
    verify_wiki(before, wiki)
    previous = json.loads((wiki / "_build/manifest.json").read_text())
    if settings.model != previous["settings"]["model"]:
        raise ValueError("Use the previous generation model to isolate source-update effects")
    before_hashes, after_hashes = source_snapshot(before), source_snapshot(after)
    if before_hashes == after_hashes:
        raise ValueError("Provide a changed source snapshot")
    output.mkdir(parents=True)
    report_path = output / "report.json"
    report = {"version": 1, "kind": "update_comparison", "status": "running",
              "before": before_hashes, "after": after_hashes, "model": settings.model,
              "previous_fingerprint": previous["fingerprint"],
              "code_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in Path(__file__).parent.glob("*.py")},
              "interval_seconds": interval,
              "timing_scope": "build and verification, including configured call delays",
              "quality": "requires_human_review", "results": []}
    write_report(report_path, report)
    try:
        for method in ("incremental", "full"):
            run_root = output / method
            sources, target = run_root / "sources", run_root / "wiki"
            shutil.copytree(after, sources)
            if source_snapshot(sources) != after_hashes:
                raise ValueError("Sources changed while copying")
            old_call_count = 0
            if method == "incremental":
                shutil.copytree(wiki, target)
                old_call_count = len(previous["calls"])
            started = time.monotonic()
            if method == "incremental":
                detail = update_wiki(sources, target, settings=settings, interval=interval,
                                     client=client_factory(), progress=progress)
            else:
                builder = WikiBuilder(sources, target, settings, interval=interval,
                                      batch_size=previous["settings"]["batch_size"],
                                      client=client_factory(), progress=progress)
                detail = builder.build()
            verification = verify_wiki(sources, target)
            elapsed = round((time.monotonic() - started) * 1000, 2)
            state = json.loads((target / "_build/manifest.json").read_text())
            calls = state["calls"][old_call_count:]
            report["results"].append({
                "method": method, "elapsed_ms": elapsed, "model_calls": len(calls),
                "usage": usage_summary(calls), "calls": calls,
                "verification": verification, "detail": detail,
                "regenerated_pages": (len(detail["regenerate_pages"]) if method == "incremental"
                                      else state["validation"]["pages"]),
                "reused_pages": len(detail["reuse_pages"]) if method == "incremental" else 0,
                "wiki": str(target), "sources": str(sources),
                "fingerprint": state["fingerprint"],
                "review": state["jobs"].get("review-deployments", {}).get("result"),
            })
            write_report(report_path, report)
        if (source_snapshot(before) != before_hashes or source_snapshot(after) != after_hashes
                or json.loads((wiki / "_build/manifest.json").read_text()) != previous):
            raise ValueError("Inputs changed during comparison")
        verify_wiki(before, wiki)
        report["status"] = "complete"
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        write_report(report_path, report)
        raise
    write_report(report_path, report)
    return report
