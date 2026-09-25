"""Follow one explicit causal incident reference from a retrieved policy guide."""

from __future__ import annotations

import re
from collections.abc import Sequence

from llm_wiki.search import SearchReader, SearchResult

POLICY_SUBJECT = re.compile(r"배포|롤백|정책|규칙|feature\s*flag", re.IGNORECASE)
CAUSE_INTENT = re.compile(r"계기|이유|이후|도입")
SPECIFIC_INCIDENT = re.compile(r"E-\d+|장애\s*리포트\s*#\d+", re.IGNORECASE)
CAUSE_LINE = re.compile(r"^\s*-\s*이유\s*:\s*(.+)$", re.MULTILINE)
INCIDENT_REFERENCE = re.compile(r"장애\s*리포트\s*#([0-9]+)(?![0-9])")


def follow_causal_reference(
    connection: SearchReader,
    query: str,
    results: Sequence[SearchResult],
    *,
    limit: int = 3,
    chunks_per_document: int = 1,
) -> list[SearchResult]:
    """Insert at most one referenced incident after its guide, within Top K.

    Only policy-cause questions qualify. Read the highest-ranked guide's explicit
    reason line, not arbitrary mentions or model-inferred links. Missing targets
    leave the original result unchanged. No recursion or answer-ID lookup.
    """
    if limit < 1 or chunks_per_document not in (1, 2):
        raise ValueError("Invalid reference retrieval limits.")
    if (
        not POLICY_SUBJECT.search(query)
        or not CAUSE_INTENT.search(query)
        or SPECIFIC_INCIDENT.search(query)
    ):
        return list(results)
    groups: dict[str, list[SearchResult]] = {}
    for result in results:
        groups.setdefault(result.document_id, []).append(result)
    anchor = next((group[0] for group in groups.values() if group[0].topic == "deploy-guide"), None)
    # Keep the anchor itself and leave room for its related incident.
    if anchor is None or list(groups).index(anchor.document_id) + 1 >= limit:
        return list(results)
    rows = connection.execute(
        "SELECT content FROM chunks WHERE document_id = %s ORDER BY chunk_index",
        (anchor.document_id,),
    ).fetchall()
    references = []
    for (content,) in rows:
        for line in CAUSE_LINE.findall(content):
            references.extend(INCIDENT_REFERENCE.findall(line))
    if not references:
        return list(results)
    # The first reference in source order is the bounded one-hop expansion.
    target = f"incident-{int(references[0]):02d}"
    if target in groups:
        return list(results)
    rows = connection.execute(
        """SELECT d.id, c.chunk_index, d.title, d.topic, d.version,
                  c.heading_path, c.content
           FROM documents d JOIN chunks c ON c.document_id = d.id
           WHERE d.id = %s AND d.topic = 'incidents'
           ORDER BY c.chunk_index LIMIT %s""",
        (target, chunks_per_document),
    ).fetchall()
    if not rows:
        return list(results)
    # This is a reference lookup, not a similarity score; expose its provenance.
    linked = [SearchResult(*row, score=0.0, reference_from=anchor.document_id) for row in rows]
    selected: list[list[SearchResult]] = []
    for document_id, chunks in groups.items():
        selected.append(chunks[:chunks_per_document])
        if document_id == anchor.document_id:
            selected.append(linked)
    return [chunk for group in selected[:limit] for chunk in group]
