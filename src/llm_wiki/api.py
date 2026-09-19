"""Minimal HTTP API for LLM Wiki search and grounded answers."""

from __future__ import annotations

from typing import Literal, Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from llm_wiki.answering import GeminiAnswerProvider, GenerationSettings, answer_with_retry
from llm_wiki.database import DatabaseSettings, connect_database
from llm_wiki.embedding import EmbeddingSettings, GeminiEmbeddingProvider
from llm_wiki.search import SearchResult, embed_query_with_retry, keyword_search, vector_search


class QueryRequest(BaseModel):
    query: str
    method: Literal["keyword", "vector"] = "vector"
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

    @classmethod
    def from_result(cls, result: SearchResult) -> SearchItem:
        return cls(
            document_id=result.document_id,
            chunk_index=result.chunk_index,
            title=result.title,
            heading_path=result.heading_path,
            content=result.content,
            score=result.score,
        )


class SearchResponse(BaseModel):
    query: str
    method: Literal["keyword", "vector"]
    results: list[SearchItem]


class AnswerResponse(BaseModel):
    query: str
    method: Literal["keyword", "vector"]
    answer: str
    sources: list[SearchItem]


class ApiService(Protocol):
    def health(self) -> None: ...

    def search(self, query: str, method: str, top_k: int) -> list[SearchResult]: ...

    def answer(self, query: str, method: str, top_k: int) -> tuple[str, list[SearchResult]]: ...


class WikiService:
    """Connect API requests to the existing retrieval and answer pipeline."""

    def health(self) -> None:
        with connect_database(DatabaseSettings.from_env()) as connection:
            connection.execute("SELECT 1").fetchone()

    def search(self, query: str, method: str, top_k: int) -> list[SearchResult]:
        with connect_database(DatabaseSettings.from_env()) as connection:
            if method == "keyword":
                return keyword_search(connection, query, limit=top_k)

            provider = GeminiEmbeddingProvider(EmbeddingSettings.from_env())
            query_vector = embed_query_with_retry(provider, query)
            return vector_search(connection, query_vector, limit=top_k)

    def answer(self, query: str, method: str, top_k: int) -> tuple[str, list[SearchResult]]:
        sources = self.search(query, method, top_k)
        provider = GeminiAnswerProvider(GenerationSettings.from_env())
        result = answer_with_retry(provider, query, sources)
        return result.answer, list(result.sources)


def create_app(service: ApiService | None = None) -> FastAPI:
    service = service or WikiService()
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
    def answer(request: QueryRequest) -> AnswerResponse:
        try:
            text, sources = service.answer(request.query, request.method, request.top_k)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="answer unavailable") from exc
        return AnswerResponse(
            query=request.query,
            method=request.method,
            answer=text,
            sources=[SearchItem.from_result(source) for source in sources],
        )

    return app


app = create_app()
