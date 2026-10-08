"""Reproducible answer comparison and human-reviewed regression cases."""
from __future__ import annotations

import hashlib
import json
import statistics
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from llm_wiki.conversation import ConversationTurn
from llm_wiki.evaluation import load_evaluation_questions
from llm_wiki.telemetry import usage_summary


class AnswerCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    group: str = "supplemental"
    query: str = Field(min_length=1, max_length=2000)
    history: list[ConversationTurn] = Field(default_factory=list, max_length=12)
    expected: str = ""
    expected_status: Literal[
        "answered", "clarification_required", "insufficient_evidence", "budget_exceeded"
    ] | None = None
    required_document_ids: list[str] = Field(default_factory=list)
    origin_answer_id: str | None = None
    evaluation_id: str | None = None


def load_cases(path):
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if "questions" in payload:
        cases = [AnswerCase(id=q.question_id, group="baseline20", query=q.query,
                            required_document_ids=list(q.required_document_ids))
                 for q in load_evaluation_questions(path)]
    else:
        cases = [AnswerCase.model_validate(c) for c in payload["cases"]]
    if not cases or len({c.id for c in cases}) != len(cases):
        raise ValueError("Cases must be nonempty with unique IDs")
    return cases


def source_snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*.md"))}


def write_report(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def evidence_ids(response):
    ids = set()
    for source in response.get("sources", []):
        if "document_id" in source:
            ids.add(source["document_id"])
        ids.update(original["document_id"] for original in source.get("originals", []))
    return ids


def summarize_answers(results):
    groups = defaultdict(list)
    for row in results:
        groups[(row["group"], row["method"])].append(row)
    summary = []
    for (group, method), rows in sorted(groups.items()):
        times = [r["elapsed_ms"] for r in rows]
        calls = [c for r in rows for c in r["response"].get("trace", {}).get("calls", [])]
        summary.append({
            "group": group, "method": method, "runs": len(rows),
            "http_errors": sum(r["http_status"] != 200 for r in rows),
            "median_ms": statistics.median(times),
            "stdev_ms": statistics.pstdev(times), "min_ms": min(times), "max_ms": max(times),
            "usage": usage_summary(calls),
            "quality": "requires_human_review",
        })
    return summary


def compare_answers(cases, methods, repeats, send, *, metadata=None, checkpoint=lambda _: None):
    if repeats < 1 or not methods or len(set(methods)) != len(methods):
        raise ValueError("Use positive repeats and unique methods")
    if set(methods) - {"keyword", "vector", "hybrid", "wiki"}:
        raise ValueError("Unsupported answer method")
    metadata = metadata or {}
    report = {"version": 1, "kind": "answer_comparison", "status": "running",
              "started_at": datetime.now(UTC).isoformat(), "settings": metadata,
              "methods": methods, "repeats": repeats,
              "cases": [c.model_dump(mode="json") for c in cases], "results": []}
    checkpoint(report)
    try:
        for repeat in range(repeats):
            for case_index, case in enumerate(cases):
                # Rotate method order to avoid always measuring Wiki after the same method.
                shift = (repeat + case_index) % len(methods)
                for method in methods[shift:] + methods[:shift]:
                    request = {"query": case.query, "method": method,
                               "history": [t.model_dump() for t in case.history],
                               "top_k": metadata.get("top_k", 3),
                               "max_input_tokens": metadata.get("max_input_tokens", 48000)}
                    started = time.monotonic()
                    code, response = send(request)
                    elapsed = round((time.monotonic() - started) * 1000, 2)
                    found = evidence_ids(response)
                    report["results"].append({
                        "case_id": case.id, "group": case.group, "repeat": repeat + 1,
                        "method": method, "request": request, "http_status": code,
                        "response": response, "elapsed_ms": elapsed,
                        "checks": {
                            "expected_status": (response.get("status") == case.expected_status
                                                if case.expected_status else None),
                            "required_sources_present": (set(case.required_document_ids) <= found
                                                         if case.required_document_ids else None),
                            "semantic_correctness": "unreviewed",
                        },
                    })
                    checkpoint(report)
        report["status"] = "complete"
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        checkpoint(report)
        raise
    report["summary"] = summarize_answers(report["results"])
    checkpoint(report)
    return report


def attach_reviews(report, store):
    """Compare only human-reviewed pairs; never promote a source-ID hit into a quality score."""
    pairs = defaultdict(dict)
    for row in report["results"]:
        key = row["response"].get("answer_id")
        try:
            evaluations = store.get(key)["evaluations"] if key else []
        except KeyError:
            evaluations = []
        row["evaluation"] = evaluations[-1] if evaluations else None
        if evaluations:
            pairs[(row["group"], row["case_id"], row["repeat"])][row["method"]] = evaluations[-1]
    changes = []
    rank = {"incorrect": 0, "partial": 1, "correct": 2}
    for (group, case, repeat), methods in pairs.items():
        if "wiki" not in methods:
            continue
        for method, evaluation in methods.items():
            if method == "wiki":
                continue
            wiki_review = methods["wiki"]
            same_criteria = (evaluation["expected"], evaluation["expected_status"]) == (
                wiki_review["expected"], wiki_review["expected_status"])
            delta = rank[wiki_review["verdict"]] - rank[evaluation["verdict"]]
            changes.append({"group": group, "case_id": case, "repeat": repeat,
                            "baseline": method, "candidate": "wiki",
                            "result": ("improved" if delta > 0 else "regressed" if delta < 0
                                       else "same") if same_criteria else "criteria_mismatch"})
    report["reviewed_comparisons"] = changes
    return report
