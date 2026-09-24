from __future__ import annotations

import os

import psycopg
import pytest

from llm_wiki.search import hybrid_search, keyword_search, vector_search
from llm_wiki.search_scope import SearchScope, infer_search_scope


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("현재 프로덕션 배포 명령은 무엇인가요?", SearchScope("latest")),
        ("요즘 배포 승인은 누구에게 받나요?", SearchScope("latest")),
        ("최신 배포 가이드의 도구는?", SearchScope("latest")),
        ("현재 배포 가이드 v22의 금지 시간은?", SearchScope("version", 22)),
        ("배포 가이드 V09에서는 어떤 도구를 쓰나요?", SearchScope("version", 9)),
        ("배포 가이드 v22에서 변경된 금지 시간은?", SearchScope("version", 22)),
        ("배포 가이드 v22가 변경된 이유는?", SearchScope()),
        ("현재 배포 규칙이 바뀐 계기는?", SearchScope()),
        ("최신 배포 정책과 v22를 비교해 줘", SearchScope()),
        ("배포 가이드 v22와 v30의 금지 시간은?", SearchScope()),
        ("예전 배포 명령은?", SearchScope()),
        ("배포 도구는 무엇인가요?", SearchScope()),
        ("현재 E-021 배포 오류는 어떻게 해결하나요?", SearchScope()),
        ("현재 데이터베이스 설정은?", SearchScope()),
    ],
)
def test_query_scope(query, expected):
    assert infer_search_scope(query) == expected


@pytest.fixture
def database():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL for isolated PostgreSQL retrieval tests.")
    with psycopg.connect(url) as connection:
        # Temporary tables shadow live tables only in this connection.
        connection.execute("""CREATE TEMP TABLE documents (
            id text PRIMARY KEY, title text, topic text, version text)""")
        connection.execute("""CREATE TEMP TABLE chunks (
            document_id text, chunk_index int, heading_path text, content text,
            tsv tsvector, embedding vector(2))""")
        for doc, topic, version, embedding in [
            ("guide-9", "deploy-guide", "9", "[1,0]"),
            ("guide-30", "deploy-guide", "30", "[0,1]"),
            ("invalid", "deploy-guide", "draft", "[1,0]"),
            ("missing", "deploy-guide", None, "[1,0]"),
            ("faq", "faq", "999", "[1,0]"),
        ]:
            add_document(connection, doc, topic, version, embedding)
        yield connection
        connection.rollback()


def add_document(connection, doc, topic, version, embedding="[0,1]"):
    connection.execute(
        "INSERT INTO documents VALUES (%s,%s,%s,%s)", (doc, "배포 가이드", topic, version)
    )
    connection.execute(
        """INSERT INTO chunks VALUES (
        %s, 0, '배포 규칙', '배포 명령', to_tsvector('simple', '배포 명령'), %s::vector)""",
        (doc, embedding),
    )


@pytest.mark.parametrize("method", ["keyword", "vector", "hybrid"])
def test_filters_candidates_before_ranking_and_tracks_future_versions(database, method):
    def retrieve(query):
        scope = infer_search_scope(query)
        if method == "keyword":
            return keyword_search(database, query, limit=1)
        if method == "vector":
            return vector_search(database, [1, 0], limit=1, scope=scope)
        return hybrid_search(database, query, [1, 0], limit=1)

    # Lexicographic max would choose 9; a post-Top1 filter would lose guide-30.
    assert retrieve("현재 배포 명령은?")[0].document_id == "guide-30"
    add_document(database, "guide-31", "deploy-guide", "31")
    assert retrieve("현재 배포 명령은?")[0].document_id == "guide-31"
    add_document(database, "guide-100", "deploy-guide", "100")
    assert retrieve("최신 배포 명령은?")[0].document_id == "guide-100"
    assert retrieve("현재 배포 가이드 v9의 명령은?")[0].document_id == "guide-9"
    assert retrieve("배포 가이드 v999의 명령은?") == []


def test_history_preserves_older_guides_and_other_topics(database):
    scope = infer_search_scope("현재 배포 정책이 변경된 이유는?")
    results = vector_search(database, [1, 0], limit=10, scope=scope)
    assert {"guide-9", "guide-30", "faq"} <= {r.document_id for r in results}
    database.execute("DELETE FROM documents WHERE topic = 'deploy-guide'")
    assert vector_search(database, [1, 0], scope=SearchScope("latest")) == []
