"""Request-local SDK accounting shared by RAG and Wiki; no API keys or prompts stored."""
from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


class InputBudgetExceeded(ValueError):
    """Cumulative generation prompt text would exceed the request budget."""


@dataclass
class RequestTrace:
    budget: int
    used: int = 0
    calls: list[dict] = field(default_factory=list)
    stages: list[dict] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    reads: list[dict] = field(default_factory=list)
    retrieved: list[dict] = field(default_factory=list)


ACTIVE: ContextVar[RequestTrace | None] = ContextVar("wiki_request_trace", default=None)


@contextmanager
def request_trace(budget):
    trace = RequestTrace(budget)
    token = ACTIVE.set(trace)
    try:
        yield trace
    finally:
        ACTIVE.reset(token)


@contextmanager
def stage(name):
    started = time.monotonic()
    try:
        yield
    finally:
        if trace := ACTIVE.get():
            trace.stages.append({"stage": name,
                                 "elapsed_ms": round((time.monotonic() - started) * 1000, 2)})


def model_call(models, operation, *, stage_name, input_tokens=None, **kwargs):
    trace = ACTIVE.get()
    if trace is None:
        return getattr(models, operation)(**kwargs)
    if operation == "generate_content":
        if input_tokens is None:
            count = model_call(models, "count_tokens", stage_name=stage_name,
                               model=kwargs["model"], contents=kwargs["contents"])
            input_tokens = count.total_tokens
        if not isinstance(input_tokens, int) or input_tokens < 0:
            raise ValueError("Provider did not return an input token count")
        if trace.used + input_tokens > trace.budget:
            raise InputBudgetExceeded("Cumulative generation input budget exceeded")
        trace.used += input_tokens
    record = {"stage": stage_name, "operation": operation, "model": kwargs["model"],
              "attempt": 1 + sum(c["operation"] == operation and c["stage"] == stage_name
                                 for c in trace.calls),
              "prompt_sha256": hashlib.sha256(str(kwargs["contents"]).encode()).hexdigest(),
              "input_text_tokens": input_tokens, "usage": None}
    started = time.monotonic()
    try:
        result = getattr(models, operation)(**kwargs)
        usage = getattr(result, "usage_metadata", None)
        record["usage"] = usage.model_dump(mode="json") if usage else None
        record["status"] = "ok"
        record["model_version"] = getattr(result, "model_version", None)
        return result
    except Exception as exc:
        record.update(status="error", error_type=type(exc).__name__)
        raise
    finally:
        record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
        trace.calls.append(record)


def usage_summary(calls):
    """Do not convert unknown provider usage into a zero-cost observation."""
    billable = [c for c in calls if c.get("operation") != "count_tokens"]
    totals = [(c.get("usage") or {}).get("total_token_count") for c in billable]
    known = [v for v in totals if isinstance(v, int)]
    return {"calls": len(billable), "known_total_tokens": sum(known),
            "missing_usage_calls": len(totals) - len(known),
            "total_tokens": sum(known) if len(known) == len(totals) else None}
