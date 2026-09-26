"""Verify a completed Wiki offline, including hashes and local Markdown targets."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from llm_wiki.wiki_content import (
    TOPICS,
    LinkBatch,
    PageBatch,
    WikiIndex,
    WikiValidationError,
    load_wiki_sources,
    render_page,
    validate_index,
    validate_links,
    validate_pages,
)
from llm_wiki.wiki_history import (
    TemporalReview,
    conflicting_values,
    deployment_history,
    review_sources,
    validate_review,
)


def matches_snapshot(raw: bytes, expected: str) -> bool:
    """Allow Git's LF/CRLF checkout conversion, but no content changes."""
    lf = raw.replace(b"\r\n", b"\n")
    return any(
        hashlib.sha256(candidate).hexdigest() == expected
        for candidate in (raw, lf, lf.replace(b"\n", b"\r\n"))
    )


def verify_wiki(source_root: Path, output: Path) -> dict:
    source_root, output = source_root.resolve(), output.resolve()
    state = json.loads((output / "_build/manifest.json").read_text(encoding="utf-8"))
    if state["status"] != "complete":
        raise WikiValidationError("Build is incomplete.")
    sources = load_wiki_sources(source_root)
    expected_sources = state["settings"]["sources"]
    if set(expected_sources) != set(sources) or any(
        not matches_snapshot(source.path.read_bytes(), expected_sources[key])
        for key, source in sources.items()
    ):
        raise WikiValidationError("Source snapshot changed.")
    groups = {
        TOPICS[s.document.topic][0]: [
            key for key, other in sources.items() if other.document.topic == s.document.topic
        ] for s in sources.values()
    }
    pages, links, indexes = [], [], []
    page_data, link_data = {}, {}
    evidence_count = 0
    for job, data in state["jobs"].items():
        if job.startswith("pages-"):
            batch = PageBatch.model_validate(data["result"])
            ids = [p.document_id for p in batch.pages]
            if any(key not in sources for key in ids):
                raise WikiValidationError("Unknown page ID in manifest.")
            validate_pages(batch, ids, sources)
            pages.extend(ids)
            page_data.update({p.document_id: p for p in batch.pages})
            evidence_count += sum(
                len(statement.evidence)
                for p in batch.pages
                for statement in [p.summary] + [s for part in p.sections for s in part.statements]
            )
        elif job.startswith("links-"):
            batch = LinkBatch.model_validate(data["result"])
            validate_links(batch, groups[job.removeprefix("links-")], sources)
            links.extend(p.document_id for p in batch.pages)
            link_data.update({p.document_id: p for p in batch.pages})
        elif job.startswith("index-"):
            folder = job.removeprefix("index-")
            validate_index(
                WikiIndex.model_validate(data["result"]),
                list(groups) if folder == "root" else groups[folder],
            )
            indexes.append(folder)
    if sorted(pages) != sorted(sources) or sorted(links) != sorted(sources):
        raise WikiValidationError("Missing or duplicate page/link records.")
    if sorted(indexes) != sorted([*groups, "root"]):
        raise WikiValidationError("Missing or duplicate indexes.")
    expected = {s.wiki_path for s in sources.values()}
    expected.update(f"{folder}/index.md" for folder in groups)
    expected.add("index.md")
    if set(state["artifacts"]) != expected:
        raise WikiValidationError("Incomplete or unexpected artifact paths.")
    actual = {p.relative_to(output).as_posix() for p in output.rglob("*.md")
              if "_build" not in p.relative_to(output).parts}
    if actual != expected:
        raise WikiValidationError("Missing or unexpected Markdown files.")
    temporal = "wiki_history.py" in state["settings"]["implementation"]
    history = deployment_history(sources) if temporal else []
    review = TemporalReview(reviewed_ids=[], findings=[])
    if temporal:
        if not state.get("temporal"):
            raise WikiValidationError("Missing temporal validation record.")
        review_input = review_sources(sources)
        if review_input:
            record = state["jobs"].get("review-deployments")
            if record is None:
                raise WikiValidationError("Missing deployment conflict review.")
            review = TemporalReview.model_validate(record["result"])
            validate_review(review, review_input)
    latest_id = max(
        (key for key, s in sources.items() if s.document.topic == "deploy-guide"),
        key=lambda key: int(sources[key].document.metadata["version"]), default=None,
    )
    link_count = 0
    for relative, expected_hash in state["artifacts"].items():
        path = output / relative
        raw = path.read_bytes()
        if not matches_snapshot(raw, expected_hash):
            raise WikiValidationError(f"Generated file changed: {relative}")
        content = raw.decode("utf-8")
        targets = re.findall(r"\]\(([^)]+)\)", content)
        targets += re.findall(r"^\[\d+\]:\s*(\S+)", content, re.MULTILINE)
        for href in targets:
            parsed = urlsplit(href)
            if parsed.scheme or parsed.netloc:
                raise WikiValidationError("Unexpected external link.")
            target = (path.parent / unquote(parsed.path)).resolve()
            if not (target.is_relative_to(output) or target.is_relative_to(source_root)):
                raise WikiValidationError("Link escapes Wiki and source directories.")
            if not target.is_file():
                raise WikiValidationError(f"Broken link in {relative}: {href}")
            if parsed.fragment:
                line_range = re.fullmatch(r"L(\d+)-L(\d+)", parsed.fragment)
                if not line_range:
                    raise WikiValidationError("Unsupported source fragment.")
                first, last = map(int, line_range.groups())
                if not 1 <= first <= last <= len(target.read_text(encoding="utf-8").splitlines()):
                    raise WikiValidationError("Source line range is out of bounds.")
            link_count += 1
        if temporal:
            key = next((key for key, s in sources.items() if s.wiki_path == relative), None)
            if key is not None:
                regenerated = render_page(
                    page_data[key], link_data[key], sources, output, latest_id, history, review,
                )
                if content.replace("\r\n", "\n") != regenerated:
                    raise WikiValidationError(f"Page differs from verified data/history: {relative}")
    return {
        "status": "PASS", "source_documents": len(sources), "pages": len(pages),
        "indexes": len(indexes), "checked_links": link_count,
        "checked_evidence_quotes": evidence_count,
        "temporal_validation": temporal,
        "rule_changes": len(history),
        "reviewed_sources": len(review.reviewed_ids),
        "conflict_candidates": len(review.findings) + (len(conflicting_values(sources)) if temporal else 0),
        "semantic_review": "Quote existence does not prove claim entailment or completeness.",
    }
