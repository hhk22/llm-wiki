"""Compare RAG/Wiki with fixed questions; real runs call paid model APIs."""
from __future__ import annotations

import argparse
import hashlib
import os
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from llm_wiki.answer_records import AnswerStore
from llm_wiki.api import create_app
from llm_wiki.database import DatabaseSettings, connect_database
from llm_wiki.documents import load_documents
from llm_wiki.embedding import EmbeddingSettings
from llm_wiki.evaluation_runs import compare_answers, load_cases, source_snapshot, write_report
from llm_wiki.storage_validation import validate_storage
from llm_wiki.wiki_validation import verify_wiki

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "evaluation/questions.yaml")
    parser.add_argument("--methods", nargs="+", choices=["keyword", "vector", "hybrid", "wiki"],
                        default=["keyword", "vector", "hybrid", "wiki"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-input-tokens", type=int, default=48000)
    parser.add_argument("--sources", type=Path, default=ROOT / "sources")
    parser.add_argument("--wiki", type=Path, default=ROOT / "wiki")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output file")
    if args.repeats < 1 or len(set(args.methods)) != len(args.methods):
        parser.error("Use positive repeats and unique methods")
    if not 1000 <= args.max_input_tokens <= 100000:
        parser.error("Input budget must be 1000..100000")
    cases = load_cases(args.cases)
    if args.dry_run:
        print(f"cases={len(cases)} requests={len(cases) * len(args.methods) * args.repeats}; "
              "no model/DB calls, no files written")
        return
    load_dotenv(ROOT / ".env")
    os.environ.update(GEMINI_GENERATION_MODEL=args.model,
                      LLM_WIKI_DIR=str(args.wiki.resolve()),
                      LLM_WIKI_SOURCES=str(args.sources.resolve()))
    snapshot = source_snapshot(args.sources)
    if not snapshot:
        parser.error("Sources must not be empty")

    def validate_inputs():
        checks = {}
        if "wiki" in args.methods:
            checks["wiki"] = verify_wiki(args.sources, args.wiki)
        if set(args.methods) - {"wiki"}:
            embedding = EmbeddingSettings.from_env()
            with connect_database(DatabaseSettings.from_env()) as conn:
                result = validate_storage(conn, load_documents(args.sources),
                                          embedding_model=embedding.model,
                                          embedding_dimensions=embedding.dimensions)
                if not result.is_valid:
                    raise ValueError("RAG database does not match source snapshot")
                checks["rag"] = asdict(result)
        return checks

    manifest = args.wiki / "_build/manifest.json"
    wiki_hash = hashlib.sha256(manifest.read_bytes()).hexdigest() if "wiki" in args.methods else None
    metadata = {"wiki_manifest_sha256": wiki_hash, "model": args.model, "max_input_tokens": args.max_input_tokens, "top_k": 3,
                "sources": snapshot, "preflight": validate_inputs(),
                "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
                "code_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (ROOT / "src/llm_wiki").glob("*.py")},
                "timing_scope": "in-process API including record persistence; no HTTP network"}
    record_path = args.output.with_suffix(".sqlite3")
    if record_path.exists():
        parser.error("Companion record database exists; choose a new output")
    metadata["record_db"] = str(record_path.resolve())
    with TestClient(create_app(record_store=AnswerStore(record_path))) as client:
        def send(request):
            response = client.post("/answer", json=request)
            body = response.json()
            if response.status_code != 200 and isinstance(body.get("detail"), dict):
                answer_id = body["detail"].get("answer_id")
                if answer_id:
                    body = client.get(f"/answers/{answer_id}").json()["response"]
            return response.status_code, body

        report = compare_answers(cases, args.methods, args.repeats, send, metadata=metadata,
                                 checkpoint=lambda r: write_report(args.output, r))
    try:
        if source_snapshot(args.sources) != snapshot:
            raise ValueError("Sources changed during comparison")
        if wiki_hash and hashlib.sha256(manifest.read_bytes()).hexdigest() != wiki_hash:
            raise ValueError("Wiki changed during comparison")
        report["postflight"] = validate_inputs()
    except Exception as exc:
        report.update(status="invalidated", error_type=type(exc).__name__)
        write_report(args.output, report)
        raise
    write_report(args.output, report)
    print(f"Saved {len(report['results'])} runs; semantic quality requires human review: {args.output}")


if __name__ == "__main__":
    main()
