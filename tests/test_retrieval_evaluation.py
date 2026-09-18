from __future__ import annotations

from llm_wiki.evaluation import EvaluationQuestion, evaluate_questions
from llm_wiki.search import SearchResult


def result(document_id: str) -> SearchResult:
    return SearchResult(
        document_id=document_id,
        chunk_index=0,
        title=document_id,
        topic="test",
        version=None,
        heading_path=document_id,
        content="content",
        score=1.0,
    )


def test_evaluation_separates_single_and_cross_reference_metrics() -> None:
    questions = [
        EvaluationQuestion("q1", "exact_keyword", "first", ("doc-a",)),
        EvaluationQuestion("q2", "paraphrase", "second", ("doc-b",)),
        EvaluationQuestion("q3", "cross_reference", "third", ("doc-a", "doc-c")),
    ]
    rankings = {
        "first": [result("doc-a"), result("doc-x"), result("doc-y")],
        "second": [result("doc-x"), result("doc-b"), result("doc-y")],
        "third": [result("doc-a"), result("doc-c"), result("doc-y")],
    }

    report = evaluate_questions(
        questions,
        lambda query, top_k: rankings[query][:top_k],
        method="test",
    )

    assert report.single_document_questions == 2
    assert report.hit_at_1_count == 1
    assert report.hit_at_1 == 0.5
    assert report.hit_at_3_count == 3
    assert report.hit_at_3 == 1.0
    assert report.results[2].hit_at_1 is None
    assert report.results[2].hit_at_3


def test_cross_reference_requires_both_documents_in_top_three() -> None:
    question = EvaluationQuestion(
        "q1",
        "cross_reference",
        "query",
        ("doc-a", "doc-b"),
    )

    report = evaluate_questions(
        [question],
        lambda query, top_k: [result("doc-a"), result("doc-x"), result("doc-y")],
        method="test",
    )

    assert not report.results[0].hit_at_3
