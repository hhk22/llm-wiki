"""Save a human evaluation, export a reviewed case, or refresh comparison judgments."""
import argparse
import json
from pathlib import Path

from llm_wiki.answer_records import AnswerStore, EvaluationInput
from llm_wiki.evaluation_runs import attach_reviews, write_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--answer-id")
    parser.add_argument("--evaluation", type=Path, help="Human-authored EvaluationInput JSON")
    parser.add_argument("--export-case", type=Path)
    parser.add_argument("--report", type=Path, help="Comparison report to attach saved judgments to")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.records.is_file():
        parser.error("Record database does not exist")
    if (args.evaluation or args.export_case) and not args.answer_id:
        parser.error("--answer-id is required")
    if args.report and not args.output:
        parser.error("--report requires a new --output path")
    for path in (args.output, args.export_case):
        if path and path.exists():
            parser.error("Output exists; preserve the previous record")
    store = AnswerStore(args.records)
    if args.evaluation:
        evaluation = EvaluationInput.model_validate_json(args.evaluation.read_text())
        print(store.evaluate(args.answer_id, evaluation)["evaluation_id"])
    if args.export_case:
        write_report(args.export_case, {"version": 1, "cases": [store.regression_case(args.answer_id)]})
    if args.report:
        report = json.loads(args.report.read_text())
        write_report(args.output, attach_reviews(report, store))
    if args.answer_id and not (args.evaluation or args.export_case):
        print(json.dumps(store.get(args.answer_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
