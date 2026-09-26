"""Structured Wiki drafts, source validation, and deterministic Markdown rendering."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from llm_wiki.documents import Document, load_documents

TOPICS = {
    "deploy-guide": ("deployments", "배포"),
    "incidents": ("incidents", "장애"),
    "error-codes": ("errors", "오류 코드"),
    "onboarding-faq": ("onboarding", "온보딩 FAQ"),
}


class WikiValidationError(ValueError):
    """Reject incomplete pages, nonexistent evidence, or invalid navigation."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(StrictModel):
    document_id: str
    quote: str = Field(min_length=6, max_length=1500)


class Statement(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    evidence: list[Evidence] = Field(min_length=1, max_length=6)


class Section(StrictModel):
    heading: str = Field(min_length=1, max_length=100)
    statements: list[Statement] = Field(min_length=1, max_length=12)


class WikiPage(StrictModel):
    document_id: str
    summary: Statement
    sections: list[Section] = Field(min_length=1, max_length=6)


class PageBatch(StrictModel):
    pages: list[WikiPage]


class RelatedPage(StrictModel):
    document_id: str
    reason: str = Field(min_length=1, max_length=180)


class PageLinks(StrictModel):
    document_id: str
    related: list[RelatedPage] = Field(max_length=8)


class LinkBatch(StrictModel):
    pages: list[PageLinks]


class IndexGroup(StrictModel):
    heading: str = Field(min_length=1, max_length=100)
    ids: list[str] = Field(min_length=1)


class WikiIndex(StrictModel):
    description: str = Field(min_length=1, max_length=300)
    groups: list[IndexGroup] = Field(min_length=1, max_length=12)


@dataclass(frozen=True)
class WikiSource:
    document: Document
    path: Path
    raw: str
    wiki_path: str


def load_wiki_sources(root: Path) -> dict[str, WikiSource]:
    sources = {}
    paths = set()
    for document in load_documents(root):
        if document.topic not in TOPICS:
            raise WikiValidationError(f"Unsupported topic: {document.topic}")
        folder = TOPICS[document.topic][0]
        if document.topic == "deploy-guide":
            name = f"v{int(document.metadata['version'])}"
        elif document.topic == "incidents":
            name = f"{int(document.metadata['number']):02d}"
        elif document.topic == "error-codes":
            name = document.document_id.removeprefix("error-")
            if not re.fullmatch(r"E-\d{3}", name):
                raise WikiValidationError("Invalid error code.")
        else:
            name = f"Q{int(document.metadata['number']):02d}"
        wiki_path = f"{folder}/{name}.md"
        if document.document_id in sources or wiki_path in paths:
            raise WikiValidationError("Duplicate source ID or Wiki path.")
        path = (root / document.source_path).resolve()
        if not path.is_relative_to(root.resolve()):
            raise WikiValidationError("Source escapes the source directory.")
        sources[document.document_id] = WikiSource(
            document, path, path.read_text(encoding="utf-8"), wiki_path
        )
        paths.add(wiki_path)
    if not sources:
        raise WikiValidationError("No Markdown sources found.")
    return sources


def plain_text(text: str) -> str:
    """LLM prose cannot inject Markdown links, headings, or HTML into the renderer."""
    if "\n" in text or "\r" in text or re.search(r"\]\s*\(|https?://|<[^>]+>", text):
        # CLI placeholders such as <id> are allowed inside backticks.
        without_code = re.sub(r"`[^`]*`", "", text)
        if "\n" in text or "\r" in text or re.search(
            r"\]\s*\(|https?://|<[^>]+>", without_code
        ):
            raise WikiValidationError("Use single-line prose without links or HTML.")
    return text.strip()


def select_context(sources: dict[str, WikiSource], targets: list[str]) -> dict[str, WikiSource]:
    """Use explicit source references, including one-hop incoming references."""
    references = {}
    for key, source in sources.items():
        text = source.document.body
        refs = {f"deploy-guide-v{int(n):02d}" for n in re.findall(r"배포 가이드\s*v(\d+)", text)}
        refs.update(f"incident-{int(n):02d}" for n in re.findall(r"장애 리포트\s*#(\d+)", text))
        refs.update(f"error-{code}" for code in re.findall(r"E-\d{3}", text))
        refs.update(f"faq-q{int(n):02d}" for n in re.findall(r"FAQ\s*Q(\d+)", text))
        references[key] = refs & sources.keys() - {key}
    selected = set(targets)
    selected.update(key for key, refs in references.items() if refs & set(targets))
    for _ in range(2):
        selected.update(target for key in list(selected) for target in references[key])
    return {key: source for key, source in sources.items() if key in selected}


def exact_ids(actual: list[str], expected: list[str]) -> None:
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise WikiValidationError("Missing, duplicate, or unexpected IDs.")


def validate_pages(batch: PageBatch, ids: list[str], sources: dict[str, WikiSource]) -> None:
    exact_ids([p.document_id for p in batch.pages], ids)
    for page in batch.pages:
        cited = set()
        for section in page.sections:
            plain_text(section.heading)
        statements = [page.summary] + [s for part in page.sections for s in part.statements]
        for statement in statements:
            plain_text(statement.text)
            for evidence in statement.evidence:
                source = sources.get(evidence.document_id)
                if source is None or evidence.quote not in source.document.body:
                    raise WikiValidationError(
                        f"{page.document_id}: quote is not verbatim in {evidence.document_id}: "
                        f"{evidence.quote[:80]!r}"
                    )
                cited.add(evidence.document_id)
        if page.document_id not in cited:
            raise WikiValidationError(f"Page does not cite its own source: {page.document_id}")
        primary = sources[page.document_id].document
        quotes = [e.quote for s in statements for e in s.evidence if e.document_id == page.document_id]
        required_lines = [
            line for line in primary.body.splitlines()
            if line.strip() and not line.startswith(("#", "**Q.", "- 이전 버전:", "- 관련 문서:"))
        ]
        missing = [line for line in required_lines if not any(line in q for q in quotes)]
        if missing:
            raise WikiValidationError(
                f"{page.document_id}: preserve these source lines in statements and evidence: {missing}"
            )
        for number in re.findall(r"장애 리포트\s*#(\d+)", primary.body):
            target = f"incident-{int(number):02d}"
            if target in sources and target != page.document_id and target not in cited:
                raise WikiValidationError(
                    f"{page.document_id}: synthesize the referenced incident's cause/response "
                    f"and cite {target}, not only its number."
                )


def validate_links(batch: LinkBatch, ids: list[str], sources: dict[str, WikiSource]) -> None:
    exact_ids([p.document_id for p in batch.pages], ids)
    for page in batch.pages:
        seen = set()
        for related in page.related:
            if (
                related.document_id not in sources
                or related.document_id == page.document_id
                or related.document_id in seen
            ):
                raise WikiValidationError("Unknown, duplicate, or self-referencing Wiki link.")
            plain_text(related.reason)
            seen.add(related.document_id)


def validate_index(index: WikiIndex, ids: list[str]) -> None:
    plain_text(index.description)
    for group in index.groups:
        plain_text(group.heading)
    exact_ids([item for group in index.groups for item in group.ids], ids)


def relative_link(origin: Path, target: Path) -> str:
    return quote(Path(os.path.relpath(target, origin.parent)).as_posix(), safe="/.-_")


def evidence_location(source: WikiSource, evidence: Evidence) -> tuple[int, int]:
    offset = source.raw.index(evidence.quote, source.raw.index(source.document.body))
    first = source.raw.count("\n", 0, offset) + 1
    last = first + evidence.quote.count("\n")
    return first, last


def render_page(
    page: WikiPage, links: PageLinks, sources: dict[str, WikiSource], output: Path,
    latest_id: str | None,
) -> str:
    source = sources[page.document_id]
    origin = output / source.wiki_path
    citations: list[Evidence] = []

    def render_statement(statement: Statement) -> str:
        refs = []
        for evidence in statement.evidence:
            if evidence not in citations:
                citations.append(evidence)
            refs.append(f"[{citations.index(evidence) + 1}]")
        return f"{plain_text(statement.text)} {' '.join(refs)}"

    lines = [f"# {source.document.title}", ""]
    if source.document.topic == "deploy-guide":
        status = "구축 원본 기준 최신" if page.document_id == latest_id else "과거 버전"
        lines += [f"> v{source.document.metadata['version']} 시점 · {status}", ""]
    lines += [render_statement(page.summary), ""]
    for section in page.sections:
        lines += [f"## {plain_text(section.heading)}", ""]
        lines += [f"- {render_statement(s)}" for s in section.statements]
        lines.append("")
    lines += ["## 관련 Wiki", ""]
    for related in links.related:
        target = sources[related.document_id]
        href = relative_link(origin, output / target.wiki_path)
        lines.append(f"- [{target.document.title}]({href}) — {plain_text(related.reason)}")
    lines += ["- [주제 목차](index.md)", "", "## 원본 출처", ""]
    for number, evidence in enumerate(citations, 1):
        target = sources[evidence.document_id]
        first, last = evidence_location(target, evidence)
        href = relative_link(origin, target.path) + f"#L{first}-L{last}"
        lines.append(f"[{number}]: {href}")
        lines.append(f"- [{number}] `{evidence.document_id}` · 원본 {first}–{last}행")
    return "\n".join(lines) + "\n"


def render_index(
    index: WikiIndex, title: str, origin: Path, entries: dict[str, tuple[str, Path, str]],
    latest: tuple[str, Path] | None = None,
) -> str:
    lines = [f"# {title}", "", plain_text(index.description), ""]
    if latest:
        lines += [f"**구축 원본 기준 최신:** [{latest[0]}]({relative_link(origin, latest[1])})", ""]
    for group in index.groups:
        lines += [f"## {plain_text(group.heading)}", ""]
        for item in group.ids:
            label, path, summary = entries[item]
            lines.append(f"- [{label}]({relative_link(origin, path)}) — {summary}")
        lines.append("")
    return "\n".join(lines)
