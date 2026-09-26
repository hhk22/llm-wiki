"""Measure the unchanged v2 vector answer path for the v3 design questions.

Run: python scripts/measure_v2_baseline.py --output evaluation/results-v2-v3-baseline.json
No conversation rewriting is implemented. The explicit diagnostic is a separate query.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import time
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from google import genai

from llm_wiki.answering import (
    GeminiAnswerProvider,
    GenerationRequestError,
    GenerationSettings,
    answer_with_retry,
)
from llm_wiki.database import DatabaseSettings, connect_database
from llm_wiki.documents import load_documents
from llm_wiki.embedding import EmbeddingRequestError, EmbeddingSettings, GeminiEmbeddingProvider
from llm_wiki.references import follow_causal_reference
from llm_wiki.search import embed_query_with_retry, vector_search
from llm_wiki.search_scope import infer_search_scope
from llm_wiki.storage_validation import validate_storage

ROOT = Path(__file__).resolve().parents[1]


class RecordedModels:
    """Observe SDK calls without changing provider requests or answers."""

    def __init__(self, client, calls):
        self.models = client.models
        self.calls = calls

    def call(self, operation, **kwargs):
        record = {"operation": operation, "model": kwargs["model"]}
        start = time.perf_counter()
        try:
            result = getattr(self.models, operation)(**kwargs)
            usage = getattr(result, "usage_metadata", None)
            record["usage"] = usage.model_dump(mode="json") if usage else None
            record["status"] = "ok"
            return result
        except Exception as exc:
            record["status"] = "error"
            record["error_type"] = type(exc).__name__
            raise
        finally:
            record["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 2)
            self.calls.append(record)

    def embed_content(self, **kwargs):
        return self.call("embed_content", **kwargs)

    def generate_content(self, **kwargs):
        return self.call("generate_content", **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("Output exists; choose a new filename to preserve measurements.")
    load_dotenv(ROOT / ".env")
    embedding = EmbeddingSettings.from_env()
    generation = GenerationSettings.from_env()
    fixture = ROOT / "evaluation/v3-baseline-questions.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))["cases"]
    payload = {
        "started_at": datetime.now(UTC).isoformat(),
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "status": "incomplete",
        "method": "vector",
        "top_k_documents": 3,
        "chunks_per_document": 2,
        "embedding_model": embedding.model,
        "embedding_dimensions": embedding.dimensions,
        "generation_model": generation.model,
        "temperature": 0,
        "history_supported": False,
        "python": platform.python_version(),
        "sdk_version": version("google-genai"),
        "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_sha256": {
            str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / "sources").rglob("*.md"))
        },
        "note": "Fresh local index; live v2 retrieval and generation. One run per query. "
        "Raw follow-up has no history. Manual rewrite is diagnostic, not condition B. "
        "Token counts are SDK-reported; null means unavailable. No monetary cost inferred.",
        "cases": [],
    }

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with connect_database(DatabaseSettings.from_env()) as connection:
        storage = validate_storage(
            connection, load_documents(ROOT / "sources"),
            embedding_model=embedding.model, embedding_dimensions=embedding.dimensions,
        )
        payload["storage_validation"] = asdict(storage)
        if not storage.is_valid:
            raise SystemExit("Storage validation failed; no evaluation calls made.")
        payload["database_versions"] = connection.execute(
            "SELECT current_setting('server_version'), extversion "
            "FROM pg_extension WHERE extname='vector'"
        ).fetchone()
        save()
        previous_generation_start = 0.0
        for case in cases:
            calls = []
            client = genai.Client(api_key=embedding.api_key)
            recorded = SimpleNamespace(models=RecordedModels(client, calls))
            embedder = GeminiEmbeddingProvider(embedding, client=recorded)
            generator = GeminiAnswerProvider(generation, client=recorded)
            record = {**case, "calls": calls, "status": "incomplete"}
            payload["cases"].append(record)
            # Respect the existing experiment's 16-second generation call spacing.
            time.sleep(max(0, 16 - (time.perf_counter() - previous_generation_start)))
            start = time.perf_counter()
            try:
                vector = embed_query_with_retry(embedder, case["query"])
                retrieval_start = time.perf_counter()
                scope = infer_search_scope(case["query"])
                sources = vector_search(
                    connection, vector, limit=3, chunks_per_document=2, scope=scope
                )
                record["scope"] = asdict(scope)
                record["sources_before_reference"] = [asdict(s) for s in sources]
                sources = follow_causal_reference(
                    connection, case["query"], sources, limit=3, chunks_per_document=2
                )
                record["retrieval_ms"] = round(
                    (time.perf_counter() - retrieval_start) * 1000, 2
                )
                record["sources"] = [asdict(s) for s in sources]
                previous_generation_start = time.perf_counter()
                result = answer_with_retry(generator, case["query"], sources)
                record["answer"] = result.answer
                record["status"] = "complete"
            except (EmbeddingRequestError, GenerationRequestError) as exc:
                record["status"] = "error"
                record["error_type"] = type(exc).__name__
                cause = exc.__cause__
                record["provider_status"] = getattr(cause, "code", None)
            finally:
                record["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 2)
                client.close()
                save()
            print(f"case={case['id']} status={record['status']}", flush=True)
    payload["status"] = (
        "complete" if all(c["status"] == "complete" for c in payload["cases"]) else "incomplete"
    )
    save()
    print(f"results={output}")
    if payload["status"] != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
