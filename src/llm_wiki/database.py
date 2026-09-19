"""PostgreSQL connection settings and idempotent schema initialization."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg
from psycopg import Connection

from llm_wiki.embedding import EMBEDDING_DIMENSIONS

SCHEMA_STATEMENTS = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE IF NOT EXISTS documents (
        id text PRIMARY KEY,
        source_path text NOT NULL UNIQUE,
        title text NOT NULL,
        topic text NOT NULL,
        version text,
        updated_at date,
        content_hash text NOT NULL CHECK (length(content_hash) = 64)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS chunks (
        document_id text NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        chunk_index integer NOT NULL CHECK (chunk_index >= 0),
        heading_path text NOT NULL,
        content text NOT NULL,
        embedding vector({EMBEDDING_DIMENSIONS}) NOT NULL,
        tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
        PRIMARY KEY (document_id, chunk_index)
    )
    """,
    "CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING GIN (tsv)",
)


class DatabaseConfigurationError(ValueError):
    """Raised when the database connection configuration is missing."""


class SqlExecutor(Protocol):
    """Small protocol shared by Psycopg connections and test doubles."""

    def execute(self, query: str, params: Any = None) -> Any: ...


@dataclass(frozen=True)
class DatabaseSettings:
    url: str

    @classmethod
    def from_env(cls) -> DatabaseSettings:
        url = os.getenv("DATABASE_URL", "").strip()
        if not url:
            raise DatabaseConfigurationError(
                "DATABASE_URL is missing. Add the local PostgreSQL URL to .env."
            )
        return cls(url=url)


def connect_database(settings: DatabaseSettings) -> Connection[Any]:
    """Open a blocking PostgreSQL connection."""
    return psycopg.connect(settings.url)


def initialize_schema(connection: SqlExecutor) -> None:
    """Create the vector extension and RAG tables when they do not exist."""
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)
