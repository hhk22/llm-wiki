"""Minimal HTTP API for LLM Wiki search and grounded answers."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Literal, Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

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
            return follow_causal_reference(
                connection,
                query,
                results,
                limit=top_k,
                chunks_per_document=chunks_per_document,
            )

    def answer(self, query: str, method: str, top_k: int) -> tuple[str, list[SearchResult]]:
        sources = self.search(
            query,
            method,
            top_k,
            chunks_per_document=ANSWER_CHUNKS_PER_DOCUMENT,
        )
        provider = GeminiAnswerProvider(GenerationSettings.from_env())
        result = answer_with_retry(provider, query, sources)
        return result.answer, list(result.sources)


def create_app(service: ApiService | None = None, *, model_factory=None, reader_factory=None) -> FastAPI:
    service = service or WikiService()
    model_factory = model_factory or (
        lambda budget: GeminiJsonModel(GenerationSettings.from_env(), budget=budget)
    )
    reader_factory = reader_factory or (lambda: WikiReader(
        Path(os.getenv("LLM_WIKI_DIR", str(ROOT / "wiki"))),
        Path(os.getenv("LLM_WIKI_SOURCES", str(ROOT / "sources"))),
    ))
    app = FastAPI(title="LLM Wiki API", version="1.0.0")

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

    @app.post("/answer", response_model=AnswerResponse)
    def answer(request: AnswerRequest) -> AnswerResponse:
        started = time.monotonic()
        model = None
        reads = []

        def response(text, sources, status, resolved_query):
            return AnswerResponse(
                query=request.query, method=request.method, answer=text, sources=sources,
                status=status, resolved_query=resolved_query,
                trace={"elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                       "calls": model.calls if model else [], "reads": reads,
                       "max_input_tokens": request.max_input_tokens,
                       "usage_scope": "question_resolution_and_wiki_only"},
            )

        try:
            if request.history or request.method == "wiki":
                model = model_factory(request.max_input_tokens)
            resolution = resolve_question(request.query, request.history, model)
            if resolution.status == "clarification_required":
                return response(resolution.clarification, [], resolution.status, None)
            query = resolution.resolved_query
            if request.method == "wiki":
                reader = reader_factory()
                reads = reader.reads
                result = answer_wiki(query, model, reader, max_pages=request.top_k)
                return response(result["answer"], result["sources"], result["status"], query)
            text, sources = service.answer(query, request.method, request.top_k)
            return response(text, [SearchItem.from_result(source) for source in sources],
                            "answered" if sources else "insufficient_evidence", query)
        except InputBudgetExceeded:
            return response("입력 토큰 한도 내에서 질문을 해석하지 못했습니다.", [],
                            "budget_exceeded", None)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="answer unavailable") from exc

    return app


app = create_app()
