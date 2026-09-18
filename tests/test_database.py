from __future__ import annotations

from typing import Any

import pytest

from llm_wiki.database import (
    EMBEDDING_DIMENSIONS,
    SCHEMA_STATEMENTS,
    DatabaseConfigurationError,
    DatabaseSettings,
    initialize_schema,
)


class RecordingConnection:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute(self, query: str, params: Any = None) -> None:
        self.queries.append(query)


def test_database_settings_are_loaded_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "  postgresql://localhost/llm_wiki  ")

    settings = DatabaseSettings.from_env()

    assert settings.url == "postgresql://localhost/llm_wiki"


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL"):
        DatabaseSettings.from_env()


def test_schema_initialization_runs_every_idempotent_statement() -> None:
    connection = RecordingConnection()

    initialize_schema(connection)

    assert tuple(connection.queries) == SCHEMA_STATEMENTS
    assert all("IF NOT EXISTS" in query for query in connection.queries)
    assert any(f"vector({EMBEDDING_DIMENSIONS})" in query for query in connection.queries)
