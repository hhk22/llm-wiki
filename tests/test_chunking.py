from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.chunking import chunk_document, chunk_documents
from llm_wiki.documents import DocumentParseError, load_document, load_documents

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "sources"


def test_deploy_guide_is_split_by_heading() -> None:
    document = load_document(SOURCE_ROOT / "deploy-guide" / "v22.md", source_root=SOURCE_ROOT)

    chunks = chunk_document(document)

    assert document.document_id == "deploy-guide-v22"
    assert document.source_path == "deploy-guide/v22.md"
    assert [chunk.heading_path for chunk in chunks] == [
        "배포 가이드 v22",
        "배포 가이드 v22 > 현재 규칙",
    ]
    assert "장애 리포트 #18 이후 결정" in chunks[0].content
    assert "배포 금지 시간: 목요일 오후, 공휴일 전날" in chunks[1].content
    assert all("---" not in chunk.content for chunk in chunks)


def test_short_incident_stays_in_one_chunk() -> None:
    document = load_document(SOURCE_ROOT / "incidents" / "18.md", source_root=SOURCE_ROOT)

    chunks = chunk_document(document)

    assert len(chunks) == 1
    assert chunks[0].heading_path == "장애 리포트 #18"
    assert "정산 배치와 배포가 같은 시간에 겹쳤다" in chunks[0].content


def test_frontmatter_preserves_hashes_and_colons_in_titles() -> None:
    incident = load_document(SOURCE_ROOT / "incidents" / "18.md", source_root=SOURCE_ROOT)
    error = load_document(SOURCE_ROOT / "error-codes" / "E-011.md", source_root=SOURCE_ROOT)

    assert incident.title == "장애 리포트 #18"
    assert error.title == "에러 코드 E-011 빌드 실패: 테스트"


def test_chunking_is_deterministic() -> None:
    document = load_document(SOURCE_ROOT / "deploy-guide" / "v22.md", source_root=SOURCE_ROOT)

    assert chunk_document(document) == chunk_document(document)


def test_all_sources_produce_expected_non_empty_chunks() -> None:
    documents = load_documents(SOURCE_ROOT)
    chunks = chunk_documents(documents)

    assert len(documents) == 120
    assert len(chunks) == 150
    assert all(chunk.content.strip() for chunk in chunks)
    assert all(chunk.heading_path for chunk in chunks)

    chunks_by_document: dict[str, list[int]] = {}
    for chunk in chunks:
        chunks_by_document.setdefault(chunk.document_id, []).append(chunk.chunk_index)
    assert all(indices == list(range(len(indices))) for indices in chunks_by_document.values())


def test_missing_frontmatter_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "invalid.md"
    path.write_text("# 제목\n\n본문", encoding="utf-8")

    with pytest.raises(DocumentParseError, match="frontmatter"):
        load_document(path)
