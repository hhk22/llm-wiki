import pytest


@pytest.fixture(autouse=True)
def isolated_answer_records(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_WIKI_RECORD_DB", str(tmp_path / "records.sqlite3"))
