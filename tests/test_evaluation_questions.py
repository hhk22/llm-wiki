from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = ROOT / "evaluation" / "questions.yaml"
SOURCE_ROOT = ROOT / "sources"

EXPECTED_TYPE_COUNTS = {
    "exact_keyword": 5,
    "paraphrase": 5,
    "current_policy": 5,
    "cross_reference": 5,
}


def load_source_ids() -> set[str]:
    source_ids: set[str] = set()
    for path in SOURCE_ROOT.rglob("*.md"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("id: "):
                source_ids.add(line.removeprefix("id: ").strip())
                break
    return source_ids


def test_evaluation_questions_are_valid() -> None:
    payload = yaml.safe_load(QUESTIONS_PATH.read_text(encoding="utf-8"))
    questions = payload["questions"]

    assert payload["version"] == 1
    assert len(questions) == 20

    ids = [question["id"] for question in questions]
    queries = [question["query"] for question in questions]
    assert len(ids) == len(set(ids))
    assert len(queries) == len(set(queries))
    assert Counter(question["type"] for question in questions) == EXPECTED_TYPE_COUNTS

    source_ids = load_source_ids()
    for question in questions:
        if question["type"] == "cross_reference":
            required_ids = question["required_document_ids"]
            assert len(required_ids) == 2
            assert set(required_ids) <= source_ids
            assert "expected_document_id" not in question
        else:
            assert question["expected_document_id"] in source_ids
            assert "required_document_ids" not in question
