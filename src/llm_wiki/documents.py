"""Load Markdown source documents and their frontmatter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED_FRONTMATTER_FIELDS = ("id", "title", "topic")


class DocumentParseError(ValueError):
    """Raised when a source document cannot be parsed."""


@dataclass(frozen=True)
class Document:
    document_id: str
    title: str
    topic: str
    source_path: str
    metadata: dict[str, Any]
    body: str


def load_document(path: Path, *, source_root: Path | None = None) -> Document:
    """Parse one Markdown document with YAML frontmatter."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    if not lines or lines[0].strip() != "---":
        raise DocumentParseError(f"{path}: YAML frontmatter must start with '---'.")

    try:
        closing_index = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
    except StopIteration as exc:
        raise DocumentParseError(f"{path}: YAML frontmatter is not closed.") from exc

    metadata = _parse_frontmatter(lines[1:closing_index], path)

    missing_fields = [field for field in REQUIRED_FRONTMATTER_FIELDS if not metadata.get(field)]
    if missing_fields:
        missing = ", ".join(missing_fields)
        raise DocumentParseError(f"{path}: missing frontmatter fields: {missing}.")

    body = "\n".join(lines[closing_index + 1 :]).strip()
    if not body:
        raise DocumentParseError(f"{path}: document body must not be empty.")

    source_path = path.relative_to(source_root).as_posix() if source_root else path.as_posix()
    return Document(
        document_id=str(metadata["id"]),
        title=str(metadata["title"]),
        topic=str(metadata["topic"]),
        source_path=source_path,
        metadata=metadata,
        body=body,
    )


def load_documents(source_root: Path) -> list[Document]:
    """Load every Markdown document below a source directory in stable path order."""
    return [
        load_document(path, source_root=source_root) for path in sorted(source_root.rglob("*.md"))
    ]


def _parse_frontmatter(lines: list[str], path: Path) -> dict[str, Any]:
    """Parse the project's flat key-value frontmatter without losing ':' or '#'."""
    metadata: dict[str, Any] = {}
    for line in lines:
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise DocumentParseError(f"{path}: invalid frontmatter line: {line!r}.")
        metadata[key.strip()] = value.strip()
    return metadata
