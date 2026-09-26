from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_wiki.answering import GenerationSettings
from llm_wiki.wiki_build import WikiBuilder, WikiBuildError, provider_schema
from llm_wiki.wiki_content import (
    Evidence,
    IndexGroup,
    LinkBatch,
    PageBatch,
    PageLinks,
    RelatedPage,
    Section,
    Statement,
    WikiIndex,
    WikiPage,
    WikiValidationError,
    load_wiki_sources,
    select_context,
    validate_index,
    validate_links,
    validate_pages,
)
from llm_wiki.wiki_validation import verify_wiki


@pytest.fixture
def source_root(tmp_path):
    root = tmp_path / "sources"
    actual = Path(__file__).resolve().parents[1] / "sources"
    for relative in ("deploy-guide/v22.md", "deploy-guide/v30.md", "incidents/18.md"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((actual / relative).read_bytes())
    return root


def drafts(sources):
    pages = {}
    for key, source in sources.items():
        statements = [Statement(text=line[2:], evidence=[Evidence(document_id=key, quote=line)])
                      for line in source.document.body.splitlines() if line.startswith("- ")]
        if key == "deploy-guide-v22":
            line = "- 원인: 정산 배치와 배포가 같은 시간에 겹쳤다. (에러 코드 E-009)"
            statements.append(Statement(
                text="정산 배치와 배포의 충돌이 변경의 계기다.",
                evidence=[Evidence(document_id="incident-18", quote=line)],
            ))
        pages[key] = WikiPage(
            document_id=key, summary=statements[0],
            sections=[Section(heading="원본 요약", statements=statements)],
        )
    return pages


class ModelQueue:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def generate_content(self, **kwargs):
        self.requests.append(kwargs)
        if not self.responses:
            raise AssertionError("Unexpected additional LLM call")
        result = self.responses.pop(0)
        return SimpleNamespace(text=result.model_dump_json(), usage_metadata=None)


def index(ids):
    return WikiIndex(description="필요한 내용을 선택한다.", groups=[IndexGroup(heading="목록", ids=ids)])


def successful_responses(sources):
    pages = drafts(sources)
    dep = [key for key in pages if key.startswith("deploy-")]
    inc = [key for key in pages if key.startswith("incident-")]
    return [
        PageBatch(pages=[pages[key] for key in dep]),
        PageBatch(pages=[pages[key] for key in inc]),
        LinkBatch(pages=[PageLinks(document_id=key, related=[
            RelatedPage(document_id="incident-18", reason="변경의 계기가 된 사건")
        ]) for key in dep]),
        index(dep),
        LinkBatch(pages=[PageLinks(document_id=key, related=[]) for key in inc]),
        index(inc),
        index(["deployments", "incidents"]),
    ]


def builder(source_root, output, responses, **kwargs):
    queue = ModelQueue(responses)
    return WikiBuilder(
        source_root, output, GenerationSettings(api_key="secret-test", model="test-model"),
        client=SimpleNamespace(models=queue), interval=0, progress=lambda *a, **k: None, **kwargs,
    ), queue


def test_build_citations_navigation_and_resume(source_root, tmp_path):
    sources = load_wiki_sources(source_root)
    output = tmp_path / "wiki"
    task, queue = builder(source_root, output, successful_responses(sources))
    report = task.build()
    assert report["pages"] == 3 and report["indexes"] == 3
    assert len(queue.requests) == 7
    text = (output / "deployments/v22.md").read_text(encoding="utf-8")
    assert "과거 버전" in text
    assert "../incidents/18.md" in text
    assert "../../sources/deploy-guide/v22.md#L" in text
    assert "v30.md" in (output / "deployments/index.md").read_text(encoding="utf-8")
    manifest = (output / "_build/manifest.json").read_text(encoding="utf-8")
    assert "secret-test" not in manifest
    assert json.loads(manifest)["status"] == "complete"
    verification = verify_wiki(source_root, output)
    assert verification["status"] == "PASS" and verification["checked_links"] > 0
    task, queue = builder(source_root, output, [], resume=True)
    task.build()
    assert queue.requests == []


def test_resume_rejects_source_change(source_root, tmp_path):
    task, _ = builder(source_root, tmp_path / "wiki", [])
    source = source_root / "deploy-guide/v22.md"
    source.write_text(source.read_text(encoding="utf-8") + "\n추가 내용", encoding="utf-8")
    with pytest.raises(WikiValidationError, match="changed"):
        builder(source_root, task.output, [], resume=True)


@pytest.mark.parametrize("bad_quote", ["원본에 없는 근거 인용이다.", "목요일 오후로 바뀌었다는 추측"])
def test_fabricated_quote_is_rejected(source_root, bad_quote):
    sources = load_wiki_sources(source_root)
    page = drafts(sources)["deploy-guide-v22"]
    page.summary.evidence[0].quote = bad_quote
    with pytest.raises(WikiValidationError, match="not verbatim"):
        validate_pages(PageBatch(pages=[page]), [page.document_id], sources)


def test_invalid_output_cannot_publish_pages_and_can_resume(source_root, tmp_path):
    sources = load_wiki_sources(source_root)
    bad = successful_responses(sources)[0]
    bad.pages[0].summary.evidence[0].document_id = "nonexistent"
    task, _ = builder(source_root, tmp_path / "wiki", [bad] * 5)
    with pytest.raises(WikiBuildError):
        task.build()
    assert not (task.output / "index.md").exists()
    state = json.loads(task.state_path.read_text(encoding="utf-8"))
    assert len(state["calls"]) == 5 and not state["jobs"]
    assert state["status"] == "building"
    task, _ = builder(source_root, task.output, successful_responses(sources), resume=True)
    task.build()
    assert (task.output / "index.md").exists()
    assert "nonexistent" in task.client.models.requests[0]["contents"]


@pytest.mark.parametrize("target", ["missing", "deploy-guide-v22", "../../outside.md"])
def test_invalid_related_page_is_rejected(source_root, target):
    sources = load_wiki_sources(source_root)
    batch = LinkBatch(pages=[PageLinks(document_id="deploy-guide-v22", related=[
        RelatedPage(document_id=target, reason="관계 설명")
    ])])
    with pytest.raises(WikiValidationError):
        validate_links(batch, ["deploy-guide-v22"], sources)


def test_index_cannot_drop_or_repeat_pages():
    with pytest.raises(WikiValidationError):
        validate_index(index(["one"]), ["one", "two"])
    with pytest.raises(WikiValidationError):
        validate_index(index(["one", "one"]), ["one"])


def test_existing_output_and_source_overlap_are_rejected(source_root, tmp_path):
    with pytest.raises(WikiValidationError, match="overlap"):
        builder(source_root, source_root / "wiki", [])
    output = tmp_path / "wiki"
    output.mkdir()
    (output / "user.md").write_text("keep", encoding="utf-8")
    with pytest.raises(WikiValidationError, match="not empty"):
        builder(source_root, output, [])
    assert (output / "user.md").read_text(encoding="utf-8") == "keep"


def test_missing_page_and_injected_link_are_rejected(source_root):
    sources = load_wiki_sources(source_root)
    page = drafts(sources)["deploy-guide-v22"]
    with pytest.raises(WikiValidationError, match="IDs"):
        validate_pages(PageBatch(pages=[page]), list(sources), sources)
    page.summary.text = "[위조 출처](https://example.com)"
    with pytest.raises(WikiValidationError, match="links"):
        validate_pages(PageBatch(pages=[page]), [page.document_id], sources)


def test_provider_schema_omits_unsupported_keys_but_keeps_nested_fields():
    schema = provider_schema(PageBatch)
    text = json.dumps(schema)
    assert "$ref" not in text and "additionalProperties" not in text
    page = schema["properties"]["pages"]["items"]
    assert set(page["required"]) == {"document_id", "summary", "sections"}
    assert page["properties"]["summary"]["properties"]["evidence"]["type"] == "array"


def test_missing_operational_rule_and_unexpanded_incident_are_rejected(source_root):
    sources = load_wiki_sources(source_root)
    page = drafts(sources)["deploy-guide-v22"]
    page.sections[0].statements.pop()
    with pytest.raises(WikiValidationError, match="referenced incident"):
        validate_pages(PageBatch(pages=[page]), [page.document_id], sources)
    page = drafts(sources)["deploy-guide-v22"]
    page.sections[0].statements = [
        s for s in page.sections[0].statements if not s.text.startswith("롤백 명령:")
    ]
    with pytest.raises(WikiValidationError, match="preserve these source lines"):
        validate_pages(PageBatch(pages=[page]), [page.document_id], sources)


def test_context_contains_causal_source_but_not_unconnected_version(source_root):
    sources = load_wiki_sources(source_root)
    context = select_context(sources, ["deploy-guide-v22"])
    assert set(context) == {"deploy-guide-v22", "incident-18"}


def test_offline_verification_detects_tampering_and_extra_files(source_root, tmp_path):
    sources = load_wiki_sources(source_root)
    task, _ = builder(source_root, tmp_path / "wiki", successful_responses(sources))
    task.build()
    path = task.output / "deployments/v22.md"
    original = path.read_bytes()
    path.write_text("changed", encoding="utf-8")
    with pytest.raises(WikiValidationError, match="changed"):
        verify_wiki(source_root, task.output)
    path.write_bytes(original)
    extra = task.output / "unexpected.md"
    extra.write_text("extra", encoding="utf-8")
    with pytest.raises(WikiValidationError, match="unexpected"):
        verify_wiki(source_root, task.output)


def test_offline_verification_detects_broken_link_despite_matching_hash(source_root, tmp_path):
    sources = load_wiki_sources(source_root)
    task, _ = builder(source_root, tmp_path / "wiki", successful_responses(sources))
    task.build()
    path = task.output / "deployments/v22.md"
    content = path.read_text(encoding="utf-8").replace("../incidents/18.md", "../incidents/99.md")
    path.write_text(content, encoding="utf-8", newline="\n")
    state = json.loads(task.state_path.read_text(encoding="utf-8"))
    state["artifacts"]["deployments/v22.md"] = hashlib.sha256(path.read_bytes()).hexdigest()
    task.state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(WikiValidationError, match="Broken link"):
        verify_wiki(source_root, task.output)


def test_offline_verification_allows_git_line_endings_but_rejects_source_edits(source_root, tmp_path):
    sources = load_wiki_sources(source_root)
    task, _ = builder(source_root, tmp_path / "wiki", successful_responses(sources))
    task.build()
    for path in [*source_root.rglob("*.md"), *task.output.rglob("*.md")]:
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b"\r\n", b"\n") if b"\r\n" in raw
                         else raw.replace(b"\n", b"\r\n"))
    assert verify_wiki(source_root, task.output)["status"] == "PASS"
    path = source_root / "deploy-guide/v22.md"
    path.write_bytes(path.read_bytes() + b"\nchanged rule\n")
    with pytest.raises(WikiValidationError, match="Source snapshot changed"):
        verify_wiki(source_root, task.output)


def test_retry_feedback_preserves_multiple_validation_errors(source_root, tmp_path):
    sources = load_wiki_sources(source_root)
    responses = successful_responses(sources)
    bad_quote = responses[0].model_copy(deep=True)
    bad_quote.pages[0].summary.evidence[0].quote = "invented quote"
    bad_link = responses[0].model_copy(deep=True)
    bad_link.pages[0].summary.text = "[injected](https://example.com)"
    task, queue = builder(source_root, tmp_path / "wiki", [bad_quote, bad_link, *responses])
    task.build()
    assert "not verbatim" in queue.requests[2]["contents"]
    assert "without links" in queue.requests[2]["contents"]


def test_code_change_requires_opt_in_and_cannot_accept_source_change(source_root, tmp_path):
    sources = load_wiki_sources(source_root)
    task, _ = builder(source_root, tmp_path / "wiki", successful_responses(sources))
    task.build()
    state = json.loads(task.state_path.read_text(encoding="utf-8"))
    state["settings"]["implementation"]["wiki_build.py"] = "earlier-code-hash"
    state["fingerprint"] = "earlier-fingerprint"
    task.state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(WikiValidationError, match="changed"):
        builder(source_root, task.output, [], resume=True)
    task, queue = builder(source_root, task.output, [], resume=True, accept_code_change=True)
    task.build()
    assert not queue.requests
    assert task.state["implementation_history"][0]["fingerprint"] == "earlier-fingerprint"
    source = source_root / "deploy-guide/v22.md"
    source.write_bytes(source.read_bytes() + b"\nchanged source\n")
    with pytest.raises(WikiValidationError, match="changed"):
        builder(source_root, task.output, [], resume=True, accept_code_change=True)
