"""Load fixed retrieval questions and calculate document-level Hit@K."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from llm_wiki.search import SearchResult

QuestionType = Literal["exact_keyword", "paraphrase", "current_policy", "cross_reference"]


@dataclass(frozen=True)
class EvaluationQuestion:
    question_id: str
    question_type: QuestionType
    query: str
    required_document_ids: tuple[str, ...]


@dataclass(frozen=True)
class QuestionResult:
    question_id: str
    question_type: QuestionType
    query: str
    required_document_ids: tuple[str, ...]
    retrieved_document_ids: tuple[str, ...]
    hit_at_1: bool | None
    hit_at_3: bool


@dataclass(frozen=True)
class TypeMetrics:
    question_type: QuestionType
    questions: int
    hit_at_1_count: int | None
    hit_at_1: float | None
    hit_at_3_count: int
    hit_at_3: float


@dataclass(frozen=True)
class EvaluationReport:
    method: str
    questions: int
    single_document_questions: int
    hit_at_1_count: int
    hit_at_1: float
    hit_at_3_count: int
    hit_at_3: float
    by_type: tuple[TypeMetrics, ...]
    results: tuple[QuestionResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SearchFunction = Callable[[str, int], Sequence[SearchResult]]


def load_evaluation_questions(path: Path) -> list[EvaluationQuestion]:
    """Load the versioned YAML question set."""
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise ValueError("Unsupported evaluation question version.")

    questions: list[EvaluationQuestion] = []
    for item in payload["questions"]:
        if item["type"] == "cross_reference":
            required_ids = tuple(item["required_document_ids"])
        else:
            required_ids = (item["expected_document_id"],)
        questions.append(
            EvaluationQuestion(
                question_id=item["id"],
                question_type=item["type"],
                query=item["query"],
                required_document_ids=required_ids,
            )
        )
    return questions


def evaluate_questions(
    questions: Sequence[EvaluationQuestion],
    search: SearchFunction,
    *,
    method: str,
    top_k: int = 3,
) -> EvaluationReport:
    """Run fixed questions and calculate retrieval metrics by scenario."""
    if top_k < 3:
        raise ValueError("top_k must be at least 3 for Hit@3 evaluation.")

    results: list[QuestionResult] = []
    for question in questions:
        matches = search(question.query, top_k)
        retrieved_ids = tuple(match.document_id for match in matches)
        required = set(question.required_document_ids)
        is_cross_reference = question.question_type == "cross_reference"
        results.append(
            QuestionResult(
                question_id=question.question_id,
                question_type=question.question_type,
                query=question.query,
                required_document_ids=question.required_document_ids,
                retrieved_document_ids=retrieved_ids,
                hit_at_1=None if is_cross_reference else bool(required & set(retrieved_ids[:1])),
                hit_at_3=required <= set(retrieved_ids[:3]),
            )
        )

    by_type = _metrics_by_type(results)
    single_results = [result for result in results if result.hit_at_1 is not None]
    hit_at_1_count = sum(result.hit_at_1 is True for result in single_results)
    hit_at_3_count = sum(result.hit_at_3 for result in results)
    return EvaluationReport(
        method=method,
        questions=len(results),
        single_document_questions=len(single_results),
        hit_at_1_count=hit_at_1_count,
        hit_at_1=_ratio(hit_at_1_count, len(single_results)),
        hit_at_3_count=hit_at_3_count,
        hit_at_3=_ratio(hit_at_3_count, len(results)),
        by_type=tuple(by_type),
        results=tuple(results),
    )


def _metrics_by_type(results: Sequence[QuestionResult]) -> list[TypeMetrics]:
    grouped: dict[QuestionType, list[QuestionResult]] = defaultdict(list)
    for result in results:
        grouped[result.question_type].append(result)

    metrics: list[TypeMetrics] = []
    for question_type in (
        "exact_keyword",
        "paraphrase",
        "current_policy",
        "cross_reference",
    ):
        type_results = grouped[question_type]
        single_results = [result for result in type_results if result.hit_at_1 is not None]
        hit_at_1_count = (
            sum(result.hit_at_1 is True for result in single_results) if single_results else None
        )
        hit_at_3_count = sum(result.hit_at_3 for result in type_results)
        metrics.append(
            TypeMetrics(
                question_type=question_type,
                questions=len(type_results),
                hit_at_1_count=hit_at_1_count,
                hit_at_1=(
                    _ratio(hit_at_1_count, len(single_results))
                    if hit_at_1_count is not None
                    else None
                ),
                hit_at_3_count=hit_at_3_count,
                hit_at_3=_ratio(hit_at_3_count, len(type_results)),
            )
        )
    return metrics


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
