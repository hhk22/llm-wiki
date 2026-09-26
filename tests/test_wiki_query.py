import hashlib
import json

import pytest
from test_conversation import ScriptedModel

from llm_wiki.conversation import InputBudgetExceeded
from llm_wiki.wiki_query import WikiReader, answer_wiki


@pytest.fixture
def reader_factory(tmp_path):
    sources = tmp_path / "sources"
    wiki = tmp_path / "wiki"
    sources.mkdir()
    (wiki / "deployments").mkdir(parents=True)
    (wiki / "_build").mkdir()
    source_hashes = {}
    pages = {
        "index.md": "# Wiki\n\n- [배포](deployments/index.md)\n",
        "deployments/index.md": "# 배포\n\n- [최신](v2.md)\n- [과거](v1.md)\n",
    }
    for version, day in [(1, "금요일"), (2, "목요일"), (3, "월요일")]:
        document_id = f"guide-{version}"
        raw = (f"---\nid: {document_id}\ntitle: 가이드 v{version}\ntopic: deploy-guide\n"
               f"version: {version}\n---\n## 현재 규칙\n- 배포 금지 시간: {day} 오후\n")
        (sources / f"v{version}.md").write_text(raw, encoding="utf-8")
        source_hashes[document_id] = hashlib.sha256(raw.encode()).hexdigest()
        related = "v1.md" if version == 2 else "v3.md"
        pages[f"deployments/v{version}.md"] = (
            f"# 가이드 v{version}\n\n> v{version} 시점\n\n## 규칙\n\n"
            f"- {day} 오후 금지. [1]\n\n## 관련 Wiki\n\n- [관련]({related})\n\n"
            f"## 원본 출처\n\n[1]: ../../sources/v{version}.md#L8-L8\n"
        )
    for path, body in pages.items():
        (wiki / path).write_text(body, encoding="utf-8")
    manifest = {"status": "complete", "settings": {"sources": source_hashes},
                "artifacts": {p: hashlib.sha256(t.encode()).hexdigest() for p, t in pages.items()}}
    (wiki / "_build/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return lambda: WikiReader(wiki, sources)


def test_navigation_one_hop_and_citations_use_read_pages(reader_factory):
    model = ScriptedModel([
        {"paths": ["deployments/index.md"]}, {"paths": ["deployments/v2.md"]},
        {"paths": ["deployments/v1.md"]},
        {"insufficient": False, "claims": [{"text": "변경 전에는 금요일 오후입니다.",
                                             "evidence_ids": ["E2"]}]},
    ])
    reader = reader_factory()
    result = answer_wiki("변경 전 금지 시간은?", model, reader)
    assert result["status"] == "answered"
    assert result["answer"].endswith("[1]")
    citation = result["sources"][0]
    assert citation.wiki_path == "deployments/v1.md"
    assert citation.section == "가이드 v1 > 규칙"
    assert citation.originals[0].document_id == "guide-1"
    assert citation.originals[0].quote == "- 배포 금지 시간: 금요일 오후"
    assert "deployments/v3.md" not in reader.cache  # linked only from the extra page
    assert reader.reads[-1]["via"] == "deployments/v2.md"


@pytest.mark.parametrize("paths", [["../../.env"], ["deployments/v3.md"],
                                   ["deployments/v2.md", "deployments/v2.md"]])
def test_unavailable_or_duplicate_selection_is_rejected(reader_factory, paths):
    model = ScriptedModel([{"paths": ["deployments/index.md"]}, {"paths": paths}])
    reader = reader_factory()
    with pytest.raises(ValueError, match="unavailable"):
        answer_wiki("질문", model, reader)
    assert all(p.endswith("index.md") for p in reader.cache)


def test_page_limit_and_insufficient_answer(reader_factory):
    model = ScriptedModel([
        {"paths": ["deployments/index.md"]}, {"paths": ["deployments/v2.md"]},
        {"insufficient": True, "claims": []},
    ])
    result = answer_wiki("원본에 없는 질문", model, reader_factory(), max_pages=1)
    assert result["sources"] == []
    assert result["status"] == "insufficient_evidence"
    assert [stage for stage, _ in model.prompts] == ["select_topics", "select_pages", "wiki_answer"]


def test_unknown_evidence_id_is_rejected(reader_factory):
    model = ScriptedModel([
        {"paths": ["deployments/index.md"]}, {"paths": ["deployments/v2.md"]},
        {"insufficient": False, "claims": [{"text": "없는 근거", "evidence_ids": ["E999"]}]},
    ])
    with pytest.raises(ValueError, match="invalid citation"):
        answer_wiki("질문", model, reader_factory(), max_pages=1)


def test_modified_wiki_and_originals_fail_closed(reader_factory):
    reader = reader_factory()
    path = reader.root / "deployments/v2.md"
    path.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        reader.read("deployments/v2.md", "test")
    source = reader.source_root / "v1.md"
    source.write_text(source.read_text(encoding="utf-8") + "changed", encoding="utf-8")
    page = reader.read("deployments/v1.md", "test")
    with pytest.raises(ValueError, match="changed"):
        reader.evidence({"deployments/v1.md": page})


def test_budget_exceeded_returns_no_uncited_answer(reader_factory):
    class Model:
        def run(self, *args):
            raise InputBudgetExceeded()

    result = answer_wiki("질문", Model(), reader_factory())
    assert result["status"] == "budget_exceeded"
    assert result["sources"] == []


def test_repository_wiki_sources_can_be_read():
    from llm_wiki.wiki_query import ROOT

    reader = WikiReader(ROOT / "wiki", ROOT / "sources")
    text = reader.read("deployments/v22.md", "test")
    evidence = reader.evidence({"deployments/v22.md": text})
    assert any("배포 금지 시간" in e.section for e in evidence.values())
    assert any(s.document_id == "incident-18" for e in evidence.values() for s in e.originals)
