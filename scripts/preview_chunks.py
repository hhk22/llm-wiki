"""Preview heading-based chunks before embedding and database storage.

Run:
    uv run python scripts/preview_chunks.py
    uv run python scripts/preview_chunks.py --document-id deploy-guide-v22
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from llm_wiki.chunking import chunk_document, chunk_documents
from llm_wiki.documents import load_documents

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "sources"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview Markdown chunking results.")
    parser.add_argument("--document-id", help="Print every chunk for one document ID.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    documents = load_documents(SOURCE_ROOT)
    chunks = chunk_documents(documents)

    print(f"documents={len(documents)}")
    print(f"chunks={len(chunks)}")

    document_counts = Counter(document.topic for document in documents)
    topic_by_document = {document.document_id: document.topic for document in documents}
    chunk_counts = Counter(topic_by_document[chunk.document_id] for chunk in chunks)
    for topic in sorted(document_counts):
        print(f"topic={topic} documents={document_counts[topic]} chunks={chunk_counts[topic]}")

    if not args.document_id:
        return

    document = next(
        (document for document in documents if document.document_id == args.document_id),
        None,
    )
    if document is None:
        raise SystemExit(f"Unknown document ID: {args.document_id}")

    print()
    for chunk in chunk_document(document):
        print(f"[{chunk.chunk_index}] {chunk.heading_path}")
        print(chunk.content)
        print()


if __name__ == "__main__":
    main()
