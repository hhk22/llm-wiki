"""Keyword, pgvector and reciprocal-rank hybrid retrieval over document chunks."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from llm_wiki.embedding import EmbeddingRequestError
from llm_wiki.search_scope import SearchScope, infer_search_scope, scope_sql

SearchMethod = Literal["keyword", "vector", "hybrid"]
HYBRID_CANDIDATE_LIMIT = 10
RRF_K = 60

ERROR_CODE_PARTICLE = re.compile(
    r"(?<![\w-])(E-\d{3})(?:에서는|에서|으로|은|는|이|가|을|를|의|과|와|도|로)"
    r"(?=$|[\s,.?!])",
    re.IGNORECASE,
)

KEYWORD_SEARCH_SQL = """
WITH query_terms AS (
    SELECT tsvector_to_array(to_tsvector('simple', %s)) AS terms
), search_query AS (
    SELECT to_tsquery('simple', array_to_string(terms, ' | ')) AS value
    FROM query_terms
    WHERE cardinality(terms) > 0
), scored AS (
    SELECT
        d.id AS document_id,
        c.chunk_index,
        d.title,
        d.topic,
        d.version,
        c.heading_path,
        c.content,
        ts_rank_cd(
            setweight(to_tsvector('simple', d.title), 'A') ||
            setweight(to_tsvector('simple', c.heading_path), 'B') ||
            setweight(c.tsv, 'C'),
            q.value
        ) AS score
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    CROSS JOIN search_query q
    WHERE (
        setweight(to_tsvector('simple', d.title), 'A') ||
        setweight(to_tsvector('simple', c.heading_path), 'B') ||
        setweight(c.tsv, 'C')
    ) @@ q.value AND ({document_filter})
), document_rank AS (
    SELECT
        *,
        row_number() OVER (
            PARTITION BY document_id
            ORDER BY score DESC, chunk_index ASC
        ) AS document_row
    FROM scored
)
SELECT
    document_id,
    chunk_index,
    title,
    topic,
    version,
    heading_path,
    content,
    score
FROM document_rank
WHERE document_row = 1
ORDER BY score DESC, document_id ASC
LIMIT %s
"""

VECTOR_SEARCH_SQL = """
WITH search_vector AS (
    SELECT %s::vector AS value
), scored AS (
    SELECT
        d.id AS document_id,
        c.chunk_index,
        d.title,
        d.topic,
        d.version,
        c.heading_path,
        c.content,
        1 - (c.embedding <=> q.value) AS score
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    CROSS JOIN search_vector q
    WHERE {document_filter}
), document_rank AS (
    SELECT
        *,
        row_number() OVER (
            PARTITION BY document_id
            ORDER BY score DESC, chunk_index ASC
        ) AS document_row
    FROM scored
)
SELECT
    document_id,
    chunk_index,
    title,
    topic,
    version,
    heading_path,
    content,
    score
FROM document_rank
WHERE document_row = 1
ORDER BY score DESC, document_id ASC
LIMIT %s
"""


class SearchReader(Protocol):
    def execute(self, query: str, params: Any = None) -> Any: ...


class QueryEmbeddingProvider(Protocol):
    def embed_query(self, query: str) -> list[float]: ...


@dataclass(frozen=True)
class SearchResult:
    document_id: str
    chunk_index: int
    title: str
    topic: str
    version: str | None
    heading_path: str
    content: str
    score: float


def normalize_keyword_query(query: str) -> str:
    """Remove known Korean particles attached to standalone error codes."""
    return ERROR_CODE_PARTICLE.sub(r"\1", query)


def keyword_search(
    connection: SearchReader,
    query: str,
    *,
    limit: int = 3,
    normalize: bool = True,
    scope: SearchScope | None = None,
) -> list[SearchResult]:
    """Search one chunk per document; disable normalization only for baseline evaluation."""
    query = _validate_query(query)
    scope = scope if scope is not None else infer_search_scope(query)
    if normalize:
        query = normalize_keyword_query(query)
    limit = _validate_limit(limit)
    predicate, params = scope_sql(scope if scope is not None else SearchScope())
    rows = connection.execute(
        KEYWORD_SEARCH_SQL.format(document_filter=predicate), (query, *params, limit)
    ).fetchall()
    return [_row_to_result(row) for row in rows]


def vector_search(
    connection: SearchReader,
    query_vector: Sequence[float],
    *,
    limit: int = 3,
    scope: SearchScope | None = None,
) -> list[SearchResult]:
    """Rank document chunks by cosine similarity, returning one chunk per document."""
    if not query_vector:
        raise ValueError("query_vector must not be empty.")
    limit = _validate_limit(limit)
    predicate, params = scope_sql(scope if scope is not None else SearchScope())
    rows = connection.execute(
        VECTOR_SEARCH_SQL.format(document_filter=predicate),
        (_serialize_vector(query_vector), *params, limit),
    ).fetchall()
    return [_row_to_result(row) for row in rows]


def fuse_rrf(
    keyword_results: Sequence[SearchResult],
    vector_results: Sequence[SearchResult],
    *,
    limit: int = 3,
    rrf_k: int = RRF_K,
) -> list[SearchResult]:
    """Fuse document ranks equally, preferring the vector source chunk on overlap."""
    limit = _validate_limit(limit)
    if rrf_k < 1:
        raise ValueError("rrf_k must be at least 1.")

    scores: dict[str, float] = {}
    representatives: dict[str, SearchResult] = {}
    for results in (keyword_results, vector_results):
        seen: set[str] = set()
        for result in results:
            document_id = result.document_id
            if document_id in seen:
                continue
            seen.add(document_id)
            scores[document_id] = scores.get(document_id, 0.0) + 1 / (rrf_k + len(seen))
            representatives[document_id] = result

    ranked_ids = sorted(scores, key=lambda document_id: (-scores[document_id], document_id))
    return [
        replace(representatives[document_id], score=scores[document_id])
        for document_id in ranked_ids[:limit]
    ]


def hybrid_search(
    connection: SearchReader,
    query: str,
    query_vector: Sequence[float],
    *,
    limit: int = 3,
    scope: SearchScope | None = None,
) -> list[SearchResult]:
    """Fuse the top ten documents per method, widening for larger requested limits."""
    query = _validate_query(query)
    limit = _validate_limit(limit)
    if not query_vector:
        raise ValueError("query_vector must not be empty.")
    candidate_limit = max(HYBRID_CANDIDATE_LIMIT, limit)
    scope = scope if scope is not None else infer_search_scope(query)
    keyword_results = keyword_search(connection, query, limit=candidate_limit, scope=scope)
    vector_results = vector_search(connection, query_vector, limit=candidate_limit, scope=scope)
    return fuse_rrf(keyword_results, vector_results, limit=limit)


def embed_query_with_retry(
    provider: QueryEmbeddingProvider,
    query: str,
    *,
    max_attempts: int = 5,
    base_delay_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> list[float]:
    """Retry rate-limit and server-side failures while embedding a query."""
    query = _validate_query(query)
    for attempt in range(1, max_attempts + 1):
        try:
            return provider.embed_query(query)
        except EmbeddingRequestError as exc:
            if not exc.retryable or attempt == max_attempts:
                raise
            sleep(base_delay_seconds * (2 ** (attempt - 1)))

    raise AssertionError("Embedding retry loop ended unexpectedly.")


def _row_to_result(row: Sequence[Any]) -> SearchResult:
    return SearchResult(
        document_id=row[0],
        chunk_index=row[1],
        title=row[2],
        topic=row[3],
        version=row[4],
        heading_path=row[5],
        content=row[6],
        score=float(row[7]),
    )


def _validate_query(query: str) -> str:
    query = query.strip()
    if not query:
        raise ValueError("query must not be empty.")
    return query


def _validate_limit(limit: int) -> int:
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    return limit


def _serialize_vector(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(value) for value in vector) + "]"
