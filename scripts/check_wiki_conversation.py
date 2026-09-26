"""Run fixed Wiki conversation probes and save answers, provenance and usage (paid API)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from llm_wiki.api import create_app

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gemini-3.5-flash-lite")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "evaluation/wiki-conversation-results.json")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    os.environ["GEMINI_GENERATION_MODEL"] = args.model
    path = ROOT / "evaluation/wiki-conversation-cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    record = {"started_at": datetime.now(UTC).isoformat(), "model": args.model,
              "cases_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "code_sha256": {name: hashlib.sha256((ROOT / "src/llm_wiki" / name).read_bytes()).hexdigest()
                              for name in ("conversation.py", "wiki_query.py", "api.py")},
              "wiki_fingerprint": json.loads((ROOT / "wiki/_build/manifest.json").read_text(
                  encoding="utf-8"))["fingerprint"], "results": []}
    previous = {}
    client = TestClient(create_app())
    for case in cases:
        history = case.get("history", [])
        if "history_from" in case:
            first = previous[case["history_from"]]
            history = [{"role": "user", "content": first["request"]["query"]},
                       {"role": "assistant", "content": first["response"].get("answer", "답변 없음")}]
        request = {"query": case["query"], "method": "wiki", "history": history}
        response = client.post("/answer", json=request)
        item = {"id": case["id"], "request": request, "http_status": response.status_code,
                "response": response.json(), "expected": case["expected"]}
        previous[case["id"]] = item
        record["results"].append(item)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        print(f"{case['id']}: HTTP {response.status_code}, "
              f"status={item['response'].get('status', 'error')}", flush=True)


if __name__ == "__main__":
    main()
