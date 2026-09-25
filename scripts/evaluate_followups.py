"""Replay recorded retrieval candidates to isolate causal references and weighted RRF.

Run: uv run python scripts/evaluate_followups.py
No embedding or generation calls. Scores belong to the cached candidate run.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from evaluate import print_report

from llm_wiki.database import DatabaseSettings, connect_database
from llm_wiki.evaluation import evaluate_questions, load_evaluation_questions
from llm_wiki.references import follow_causal_reference
from llm_wiki.search import HYBRID_VECTOR_WEIGHT, SearchResult, fuse_rrf

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "evaluation/results-v2-filters.json")
    parser.add_argument("--questions", type=Path, default=ROOT / "evaluation/questions.yaml")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "evaluation/results-v2-followups.json"
    )
    args = parser.parse_args()
    if args.output.resolve() in {args.input.resolve(), args.questions.resolve()}:
        parser.error("Output must differ from inputs.")
    load_dotenv()
    cached = json.loads(args.input.read_text())
    questions = load_evaluation_questions(args.questions)
    by_id = {q.question_id: q for q in questions}
    rankings = {}
    diagnostics = []
    with connect_database(DatabaseSettings.from_env()) as connection:
        stored = {
            (row[0], row[1]): row
            for row in connection.execute("""
            SELECT d.id,c.chunk_index,d.title,d.topic,d.version,c.content
            FROM documents d JOIN chunks c ON c.document_id=d.id
        """).fetchall()
        }

        def hydrate(rows):
            result = []
            for row in rows:
                current = stored[(row["document_id"], row["chunk_index"])]
                if row["content"] != current[5]:
                    raise ValueError("Cached candidates differ from DB; refresh the candidate run.")
                result.append(
                    SearchResult(
                        document_id=row["document_id"],
                        chunk_index=row["chunk_index"],
                        title=current[2],
                        topic=current[3],
                        version=current[4],
                        heading_path=row["heading_path"],
                        content=row["content"],
                        score=row["score"],
                    )
                )
            return result

        for item in cached["diagnostics"]:
            query = item["query"]
            assert query == by_id[item["question_id"]].query
            keyword = hydrate(item["candidates"]["keyword_filtered"])
            vector = hydrate(item["candidates"]["vector_filtered"])
            variants = {
                "keyword": keyword[:3],
                "vector": vector[:3],
                "hybrid_equal": fuse_rrf(keyword, vector),
                "hybrid_weighted": fuse_rrf(keyword, vector, vector_weight=HYBRID_VECTOR_WEIGHT),
            }
            for method, results in list(variants.items()):
                variants[method + "_references"] = follow_causal_reference(
                    connection, query, results
                )
            for method, results in variants.items():
                rankings.setdefault(method, {})[query] = results
            diagnostics.append(
                {
                    "question_id": item["question_id"],
                    "query": query,
                    "results": {
                        method: [asdict(r) for r in results] for method, results in variants.items()
                    },
                }
            )
    reports = [
        evaluate_questions(
            questions,
            lambda query, top_k, m=method: rankings[m][query][:top_k],
            method=method,
        )
        for method in rankings
    ]
    report_by_method = {r.method: r for r in reports}
    comparisons = {}
    for before, after in [
        ("vector", "vector_references"),
        ("hybrid_equal", "hybrid_weighted"),
        ("hybrid_weighted", "hybrid_weighted_references"),
    ]:
        old = {r.question_id: r for r in report_by_method[before].results}
        new = report_by_method[after].results
        comparisons[f"{before} -> {after}"] = {
            metric: {
                "improved": [
                    r.question_id
                    for r in new
                    if getattr(r, metric) is True and getattr(old[r.question_id], metric) is False
                ],
                "regressed": [
                    r.question_id
                    for r in new
                    if getattr(r, metric) is False and getattr(old[r.question_id], metric) is True
                ],
            }
            for metric in ("hit_at_1", "hit_at_3")
        }
    payload = {
        "candidate_source": args.input.resolve().relative_to(ROOT).as_posix(),
        "question_set": args.questions.resolve().relative_to(ROOT).as_posix(),
        "evaluation_mode": "cached-candidate replay with live DB reference lookup",
        "embedding_calls": 0,
        "generation_calls": 0,
        "candidate_limit_per_method": 10,
        "top_k_documents": 3,
        "chunks_per_document": 1,
        "rrf_k": 60,
        "equal_weights": {"keyword": 1, "vector": 1},
        "weighted_weights": {"keyword": 1, "vector": HYBRID_VECTOR_WEIGHT},
        "reference_rule": "one incident from the highest-ranked guide reason line, one hop, within Top K",
        "note": "Development regression set; weight selected after inspecting its ranks. "
        "This is retrieval evidence, not measured answer improvement or a held-out benchmark.",
        "reports": [r.to_dict() for r in reports],
        "comparisons": comparisons,
        "diagnostics": diagnostics,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    for report in reports:
        print_report(report)
    print(json.dumps(comparisons, ensure_ascii=False))


if __name__ == "__main__":
    main()
