"""Minimal HTTP API for LLM Wiki search and grounded answers."""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from llm_wiki.answer_records import AnswerStore, EvaluationInput, FeedbackInput
from llm_wiki.answering import GeminiAnswerProvider, GenerationSettings, answer_with_retry
from llm_wiki.conversation import (
    AnswerMethod,
    ConversationTurn,
    GeminiJsonModel,
    InputBudgetExceeded,
    resolve_question,
)
from llm_wiki.database import DatabaseSettings, connect_database
from llm_wiki.embedding import EmbeddingSettings, GeminiEmbeddingProvider
from llm_wiki.references import follow_causal_reference
from llm_wiki.search import (
    ANSWER_CHUNKS_PER_DOCUMENT,
    SearchMethod,
    SearchResult,
    embed_query_with_retry,
    hybrid_search,
    keyword_search,
    vector_search,
)
from llm_wiki.search_scope import infer_search_scope
from llm_wiki.telemetry import ACTIVE, request_trace, stage, usage_summary
from llm_wiki.wiki_query import ROOT, WikiCitation, WikiReader, answer_wiki


class QueryRequest(BaseModel):
    query: str
    method: SearchMethod = "vector"
    top_k: int = Field(default=3, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


class SearchItem(BaseModel):
    document_id: str
    chunk_index: int
    title: str
    heading_path: str
    content: str
    score: float
    reference_from: str | None = None

    @classmethod
    def from_result(cls, result: SearchResult) -> SearchItem:
        return cls(
            document_id=result.document_id,
            chunk_index=result.chunk_index,
            title=result.title,
            heading_path=result.heading_path,
            content=result.content,
            score=result.score,
            reference_from=result.reference_from,
        )


class AnswerRequest(QueryRequest):
    query: str = Field(min_length=1, max_length=2000)
    method: AnswerMethod = "vector"
    history: list[ConversationTurn] = Field(default_factory=list, max_length=12)
    max_input_tokens: int = Field(default=48000, ge=1000, le=100000)

    @model_validator(mode="after")
    def bounded(self):
        if sum(len(turn.content) for turn in self.history) > 16000:
            raise ValueError("history must contain at most 16000 characters")
        if self.method == "wiki" and self.top_k > 3:
            raise ValueError("Wiki top_k must be 1..3")
        return self


class SearchResponse(BaseModel):
    query: str
    method: SearchMethod
    results: list[SearchItem]


class AnswerResponse(BaseModel):
    answer_id: str | None = None
    query: str
    method: AnswerMethod
    answer: str
    sources: list[SearchItem | WikiCitation]
    resolved_query: str | None = None
    status: Literal[
        "answered", "clarification_required", "insufficient_evidence", "budget_exceeded"
    ] = "answered"
    trace: dict = Field(default_factory=dict)


class ApiService(Protocol):
    def health(self) -> None: ...

    def search(self, query: str, method: str, top_k: int) -> list[SearchResult]: ...

    def answer(self, query: str, method: str, top_k: int) -> tuple[str, list[SearchResult]]: ...


class WikiService:
    """Connect API requests to the existing retrieval and answer pipeline."""

    def health(self) -> None:
        with connect_database(DatabaseSettings.from_env()) as connection:
            connection.execute("SELECT 1").fetchone()

    def search(
        self,
        query: str,
        method: str,
        top_k: int,
        *,
        chunks_per_document: int = 1,
    ) -> list[SearchResult]:
        with connect_database(DatabaseSettings.from_env()) as connection:
            if ACTIVE.get():
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            if method == "keyword":
                results = keyword_search(
                    connection,
                    query,
                    limit=top_k,
                    chunks_per_document=chunks_per_document,
                )
            else:
                provider = GeminiEmbeddingProvider(EmbeddingSettings.from_env())
                query_vector = embed_query_with_retry(provider, query)
                if method == "hybrid":
                    results = hybrid_search(
                        connection,
                        query,
                        query_vector,
                        limit=top_k,
                        chunks_per_document=chunks_per_document,
                    )
                else:
                    results = vector_search(
                        connection,
                        query_vector,
                        limit=top_k,
                        scope=infer_search_scope(query),
                        chunks_per_document=chunks_per_document,
                    )
            results = follow_causal_reference(
                connection,
                query,
                results,
                limit=top_k,
                chunks_per_document=chunks_per_document,
            )
            if trace := ACTIVE.get():
                ids = list({item.document_id for item in results})
                rows = connection.execute(
                    "SELECT id, content_hash FROM documents WHERE id = ANY(%s) ORDER BY id",
                    (ids,),
                ).fetchall()
                trace.provenance["retrieved_document_hashes"] = dict(rows)
            return results

    def answer(self, query: str, method: str, top_k: int) -> tuple[str, list[SearchResult]]:
        with stage("retrieval"):
            sources = self.search(
                query, method, top_k, chunks_per_document=ANSWER_CHUNKS_PER_DOCUMENT,
            )
        if trace := ACTIVE.get():
            trace.retrieved = [asdict(source) for source in sources]
        provider = GeminiAnswerProvider(GenerationSettings.from_env())
        with stage("rag_answer"):
            result = answer_with_retry(provider, query, sources)
        return result.answer, list(result.sources)


def create_app(service: ApiService | None = None, *, model_factory=None, reader_factory=None,
               record_store: AnswerStore | None = None) -> FastAPI:
    service = service or WikiService()
    model_factory = model_factory or (
        lambda budget: GeminiJsonModel(GenerationSettings.from_env(), budget=budget)
    )
    reader_factory = reader_factory or (lambda: WikiReader(
        Path(os.getenv("LLM_WIKI_DIR", str(ROOT / "wiki"))),
        Path(os.getenv("LLM_WIKI_SOURCES", str(ROOT / "sources"))),
    ))
    store = record_store or AnswerStore(Path(os.getenv(
        "LLM_WIKI_RECORD_DB", str(ROOT / ".local/evaluation.sqlite3"))))
    code_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in Path(__file__).parent.glob("*.py")}
    app = FastAPI(title="LLM Wiki API", version="1.0.0")

    @app.get("/answers/{answer_id}")
    def get_answer(answer_id: UUID):
        try:
            return store.get(answer_id.hex)
        except KeyError:
            raise HTTPException(status_code=404, detail="answer not found") from None

    @app.post("/answers/{answer_id}/feedback", status_code=201)
    def feedback(answer_id: UUID, body: FeedbackInput):
        try:
            return store.add_feedback(answer_id.hex, body)
        except KeyError:
            raise HTTPException(status_code=404, detail="answer not found") from None

    @app.post("/answers/{answer_id}/evaluation", status_code=201)
    def evaluate(answer_id: UUID, body: EvaluationInput):
        try:
            return store.evaluate(answer_id.hex, body)
        except KeyError:
            raise HTTPException(status_code=404, detail="answer not found") from None

    @app.get("/answers/{answer_id}/regression-case")
    def regression_case(answer_id: UUID):
        try:
            return store.regression_case(answer_id.hex)
        except KeyError:
            raise HTTPException(status_code=404, detail="answer not found") from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @app.get("/health")
    def health() -> dict[str, str]:
        try:
            service.health()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {"status": "ok"}

    @app.post("/search", response_model=SearchResponse)
    def search(request: QueryRequest) -> SearchResponse:
        try:
            results = service.search(request.query, request.method, request.top_k)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="search unavailable") from exc
        return SearchResponse(
            query=request.query,
            method=request.method,
            results=[SearchItem.from_result(result) for result in results],
        )

    def execute_answer(request: AnswerRequest, trace) -> AnswerResponse:
        started = time.monotonic()
        model = None
        reads = []

        def response(text, sources, status, resolved_query):
            return AnswerResponse(
                query=request.query, method=request.method, answer=text, sources=sources,
                status=status, resolved_query=resolved_query,
                trace={"elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                       "calls": trace.calls, "reads": reads, "stages": trace.stages,
                       "retrieved": trace.retrieved,
                       "input_text_tokens": trace.used,
                       "max_input_tokens": request.max_input_tokens,
                       "usage": usage_summary(trace.calls),
                       "usage_scope": "observed_sdk_calls",
                       "budget_scope": "generation_prompt_text_including_retries",
                       "provenance": trace.provenance},
            )

        try:
            if request.history or request.method == "wiki":
                model = model_factory(request.max_input_tokens)
            with stage("question_resolution"):
                resolution = resolve_question(request.query, request.history, model)
            if resolution.status == "clarification_required":
                return response(resolution.clarification, [], resolution.status, None)
            query = resolution.resolved_query
            trace.provenance["resolved_query"] = query
            if request.method == "wiki":
                reader = reader_factory()
                reads = reader.reads
                trace.reads = reads
                trace.provenance.update(
                    wiki_fingerprint=reader.manifest.get("fingerprint"),
                    source_hashes=reader.manifest.get("settings", {}).get("sources", {}),
                )
                with stage("wiki_navigation_and_answer"):
                    result = answer_wiki(query, model, reader, max_pages=request.top_k)
                return response(result["answer"], result["sources"], result["status"], query)
            text, sources = service.answer(query, request.method, request.top_k)
            return response(text, [SearchItem.from_result(source) for source in sources],
                            "answered" if sources else "insufficient_evidence", query)
        except InputBudgetExceeded:
            return response("질문 해석·답변 과정에서 입력 토큰 한도를 초과했습니다.", [],
                            "budget_exceeded", None)

    @app.post("/answer", response_model=AnswerResponse)
    def answer(request: AnswerRequest) -> AnswerResponse:
        answer_id = uuid4().hex
        started = time.monotonic()
        with request_trace(request.max_input_tokens) as trace:
            error = None
            try:
                result = execute_answer(request, trace)
                result.answer_id = answer_id
                payload = result.model_dump(mode="json")
            except Exception as exc:  # noqa: BLE001 - persist failure, then return HTTP 502
                error = type(exc).__name__
                payload = {"answer_id": answer_id, "status": "error",
                           "error_type": error, "trace": {
                               "calls": trace.calls, "stages": trace.stages,
                               "reads": trace.reads, "retrieved": trace.retrieved,
                               "provenance": trace.provenance, "input_text_tokens": trace.used,
                               "usage": usage_summary(trace.calls),
                               "elapsed_ms": round((time.monotonic() - started) * 1000, 2)}}
            try:
                store.save(answer_id, {"request": request.model_dump(mode="json"),
                                       "response": payload, "code_sha256": code_hashes})
            except Exception as exc:
                raise HTTPException(status_code=503, detail="answer record unavailable") from exc
            if error:
                raise HTTPException(status_code=502, detail={
                    "message": "answer unavailable", "answer_id": answer_id})
            return result

    return app


app = create_app()
