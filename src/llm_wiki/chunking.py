"""Split Markdown documents into heading-based retrieval chunks."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from llm_wiki.documents import Document

HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass(frozen=True)
class Chunk:
    document_id: str
    chunk_index: int
    title: str
    heading_path: str
    content: str
    source_path: str


def chunk_document(document: Document) -> list[Chunk]:
    """Create one chunk per non-empty Markdown heading section."""
    sections: list[tuple[str, str]] = []
    heading_stack: list[tuple[int, str]] = []
    current_heading_path = document.title
    content_lines: list[str] = []

    def flush_section() -> None:
        content = "\n".join(content_lines).strip()
        if content:
            sections.append((current_heading_path, content))
        content_lines.clear()

    for line in document.body.splitlines():
        heading_match = HEADING_PATTERN.match(line)
        if not heading_match:
            content_lines.append(line)
            continue

        flush_section()
        level = len(heading_match.group(1))
        heading = heading_match.group(2).strip()
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        heading_stack.append((level, heading))
        current_heading_path = " > ".join(title for _, title in heading_stack)

    flush_section()

    return [
        Chunk(
            document_id=document.document_id,
            chunk_index=index,
            title=document.title,
            heading_path=heading_path,
            content=content,
            source_path=document.source_path,
        )
        for index, (heading_path, content) in enumerate(sections)
    ]


def chunk_documents(documents: Iterable[Document]) -> list[Chunk]:
    """Chunk documents while preserving their input order."""
    return [chunk for document in documents for chunk in chunk_document(document)]
