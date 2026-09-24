"""Conservative query rules for the single deploy-guide version family."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

VERSION = re.compile(r"(?<![\w-])v([0-9]+)(?![0-9A-Za-z_.-])", re.IGNORECASE)
HISTORY = re.compile(r"계기|이유|원인|이력|역사|과거|예전|이전|당시|비교|차이|이후|도입|변천")
CURRENT = re.compile(r"현재|최신|지금|요즘|오늘|현행")


@dataclass(frozen=True)
class SearchScope:
    mode: Literal["all", "latest", "version"] = "all"
    version: int | None = None


def infer_search_scope(query: str) -> SearchScope:
    """Keep ambiguous/history queries unrestricted; never infer a default latest version."""
    if "배포" not in query or HISTORY.search(query):
        return SearchScope()
    versions = {int(match) for match in VERSION.findall(query)}
    if len(versions) == 1:
        return SearchScope("version", versions.pop())
    if len(versions) > 1:
        return SearchScope()
    # A code/incident/FAQ question must retain non-guide evidence even with "현재".
    if re.search(r"E-\d+|Q\d+|#\d+|오류|장애|FAQ", query, re.IGNORECASE):
        return SearchScope()
    if CURRENT.search(query):
        return SearchScope("latest")
    return SearchScope()


def scope_sql(scope: SearchScope) -> tuple[str, tuple[object, ...]]:
    """Return a parameterized predicate applied before chunk ranking and LIMIT.

    Versions are numeric text in the ingestion schema; malformed/null versions
    are ineligible. CASE prevents invalid casts regardless of planner ordering.
    """
    if scope.mode == "all":
        return "TRUE", ()
    numeric_version = "CASE WHEN version ~ '^[0-9]+$' THEN version::numeric END"
    document_version = "CASE WHEN d.version ~ '^[0-9]+$' THEN d.version::numeric END"
    if scope.mode == "latest":
        return (
            (
                f"d.topic = %s AND ({document_version}) = "
                f"(SELECT max({numeric_version}) FROM documents WHERE topic = %s)"
            ),
            ("deploy-guide", "deploy-guide"),
        )
    if scope.mode == "version" and scope.version is not None and scope.version >= 0:
        return f"d.topic = %s AND ({document_version}) = %s", ("deploy-guide", scope.version)
    raise ValueError("Invalid search scope.")
