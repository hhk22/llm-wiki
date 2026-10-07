"""Durable local answer/feedback records, independent of the retrieval database."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def now():
    return datetime.now(UTC).isoformat()


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: Literal["helpful", "unhelpful"]
    category: Literal["interpretation", "retrieval", "answer", "latency", "other"] = "other"
    comment: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def reason_required(self):
        self.comment = self.comment.strip()
        if self.rating == "unhelpful" and not self.comment:
            raise ValueError("Unhelpful feedback requires a reason")
        return self


class EvaluationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: str = Field(min_length=1, max_length=100)
    verdict: Literal["correct", "partial", "incorrect"]
    evidence: Literal["supported", "mixed", "unsupported", "not_applicable"]
    reason: str = Field(min_length=1, max_length=4000)
    expected: str = Field(min_length=1, max_length=4000)
    expected_status: Literal[
        "answered", "clarification_required", "insufficient_evidence", "budget_exceeded"
    ] = "answered"

    @model_validator(mode="after")
    def nonblank(self):
        for key in ("reviewer", "reason", "expected"):
            value = getattr(self, key).strip()
            if not value:
                raise ValueError(f"{key} must not be blank")
            setattr(self, key, value)
        return self


class AnswerStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS answers (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY, answer_id TEXT NOT NULL REFERENCES answers(id),
                    created_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS feedback_answer ON feedback(answer_id);
                CREATE TABLE IF NOT EXISTS evaluations (
                    id TEXT PRIMARY KEY, answer_id TEXT NOT NULL REFERENCES answers(id),
                    created_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS evaluations_answer ON evaluations(answer_id);
            """)
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, answer_id, payload):
        with self.connection() as conn:
            conn.execute("INSERT INTO answers VALUES (?, ?, ?)",
                         (answer_id, now(), json.dumps(payload, ensure_ascii=False)))

    def get(self, answer_id):
        with self.connection() as conn:
            row = conn.execute("SELECT created_at, payload FROM answers WHERE id=?",
                               (answer_id,)).fetchone()
            if row is None:
                raise KeyError(answer_id)
            feedback = conn.execute(
                "SELECT id, created_at, payload FROM feedback WHERE answer_id=? "
                "ORDER BY created_at, id", (answer_id,)).fetchall()
            evaluations = conn.execute(
                "SELECT id, created_at, payload FROM evaluations WHERE answer_id=? "
                "ORDER BY created_at, id", (answer_id,)).fetchall()
        return {"answer_id": answer_id, "created_at": row[0], **json.loads(row[1]),
                "evaluations": [{"evaluation_id": e[0], "created_at": e[1], **json.loads(e[2])}
                                for e in evaluations],
                "feedback": [{"feedback_id": f[0], "created_at": f[1], **json.loads(f[2])}
                             for f in feedback]}

    def add_feedback(self, answer_id, feedback: FeedbackInput):
        item = {"feedback_id": uuid4().hex, "answer_id": answer_id, "created_at": now(),
                **feedback.model_dump()}
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM answers WHERE id=?", (answer_id,)).fetchone():
                raise KeyError(answer_id)
            conn.execute("INSERT INTO feedback VALUES (?, ?, ?, ?)",
                         (item["feedback_id"], answer_id, item["created_at"],
                          json.dumps(feedback.model_dump(), ensure_ascii=False)))
        return item

    def evaluate(self, answer_id, evaluation: EvaluationInput):
        item = {"evaluation_id": uuid4().hex, "answer_id": answer_id, "created_at": now(),
                **evaluation.model_dump()}
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM answers WHERE id=?", (answer_id,)).fetchone():
                raise KeyError(answer_id)
            conn.execute("INSERT INTO evaluations VALUES (?, ?, ?, ?)",
                         (item["evaluation_id"], answer_id, item["created_at"],
                          json.dumps(evaluation.model_dump(), ensure_ascii=False)))
        return item

    def regression_case(self, answer_id):
        record = self.get(answer_id)
        if not record["evaluations"]:
            raise ValueError("A human evaluation is required before exporting a case")
        review = record["evaluations"][-1]
        return {"id": "feedback-" + answer_id, "group": "feedback",
                "query": record["request"]["query"],
                "history": record["request"].get("history", []),
                "expected": review["expected"], "expected_status": review["expected_status"],
                "origin_answer_id": answer_id, "evaluation_id": review["evaluation_id"]}
