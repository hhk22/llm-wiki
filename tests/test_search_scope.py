from __future__ import annotations

import os
from dataclasses import replace

import psycopg
import pytest

from llm_wiki.references import follow_causal_reference
from llm_wiki.search import SearchResult, hybrid_search, keyword_search, vector_search
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


@pytest.mark.parametrize("method", ["keyword", "vector", "hybrid"])
def test_two_chunks_preserve_document_order_and_do_not_consume_document_slots(database, method):
    for index in (1, 2):
        database.execute(
            """INSERT INTO chunks VALUES (%s, %s, '배포 규칙', '배포 배포 명령',
            to_tsvector('simple', '배포 배포 명령'), '[1,0]'::vector)""",
            ("guide-30", index),
        )

    def retrieve(count, scope):
        options = {"limit": 3, "scope": scope, "chunks_per_document": count}
        if method == "keyword":
            return keyword_search(database, "배포 명령", **options)
        if method == "vector":
            return vector_search(database, [1, 0], **options)
        return hybrid_search(database, "배포 명령", [1, 0], **options)

    before = retrieve(1, SearchScope())
    after = retrieve(2, SearchScope())
    doc_ids = list(dict.fromkeys(r.document_id for r in after))
    assert doc_ids == [r.document_id for r in before]
    assert len(doc_ids) == 3
    for document in before:
        chunks = [r for r in after if r.document_id == document.document_id]
        assert 1 <= len(chunks) <= 2
        assert chunks[0] == document
        assert len({r.chunk_index for r in chunks}) == len(chunks)
    # Highest two chunks tie: chunk_index remains the stable secondary order.
    latest = retrieve(2, SearchScope("latest"))
    assert [(r.document_id, r.chunk_index) for r in latest] == [
        ("guide-30", 1),
        ("guide-30", 2),
    ]
    assert retrieve(2, SearchScope("version", 999)) == []


def result(doc, topic="deploy-guide", chunk=0):
    return SearchResult(doc, chunk, doc, topic, None, "", "content", 0.8)


def prepare(database, *, exists=True, content="- 이유: 장애 리포트 #18 이후 결정"):
    database.execute("UPDATE chunks SET content = %s WHERE document_id = %s", (content, "guide-30"))
    if exists:
        add_document(database, "incident-18", "incidents", None)
    return [result("guide-30"), result("faq", "faq"), result("guide-9")]


def test_causal_reference_adds_missing_incident_without_answer_id_lookup(database):
    before = prepare(database)
    after = follow_causal_reference(database, "배포 규칙이 바뀐 계기는?", before)
    assert [r.document_id for r in after] == ["guide-30", "incident-18", "faq"]
    assert after[0] == before[0]
    assert after[1].reference_from == "guide-30"
    assert after[1].score == 0


@pytest.mark.parametrize(
    "query",
    [
        "현재 배포 명령은?",
        "배포 가이드 v9의 규칙은?",
        "장애 리포트 #18의 원인과 배포 조치는?",
        "E-021 배포 오류의 이유는?",
    ],
)
def test_non_policy_cause_queries_do_not_follow_links(database, query):
    before = prepare(database)
    assert follow_causal_reference(database, query, before) == before


@pytest.mark.parametrize(
    "content",
    [
        "- 관련 장애: 장애 리포트 #18",
        "- 이유: 링크 없음",
        "- 이전 버전: 배포 가이드 v9",
    ],
)
def test_only_explicit_reason_line_is_followed(database, content):
    before = prepare(database, content=content)
    assert follow_causal_reference(database, "배포 규칙 변경 이유는?", before) == before


def test_missing_reference_target_leaves_results_unchanged(database):
    before = prepare(database, exists=False)
    assert follow_causal_reference(database, "배포 규칙 변경 이유는?", before) == before


def test_existing_reference_and_top_one_leave_results_unchanged(database):
    before = prepare(database)
    existing = [before[0], result("incident-18", "incidents"), before[1]]
    assert follow_causal_reference(database, "배포 규칙 변경 이유는?", existing) == existing
    assert (
        follow_causal_reference(database, "배포 규칙 변경 이유는?", before[:1], limit=1)
        == before[:1]
    )


def test_reference_expansion_preserves_chunk_groups_and_follows_only_one_hop(database):
    before = prepare(database)
    database.execute(
        "UPDATE chunks SET content = '- 이유: 장애 리포트 #19' WHERE document_id = 'incident-18'"
    )
    add_document(database, "incident-19", "incidents", None)
    expanded = [before[0], replace(before[0], chunk_index=1), *before[1:]]
    after = follow_causal_reference(
        database, "배포 변경 이유는?", expanded, limit=2, chunks_per_document=2
    )
    assert [(r.document_id, r.chunk_index) for r in after] == [
        ("guide-30", 0),
        ("guide-30", 1),
        ("incident-18", 0),
    ]
