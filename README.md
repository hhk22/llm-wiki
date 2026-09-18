# LLM Wiki

가상 사내 문서로 RAG 검색의 기준선을 만들고, 검색 품질과 운영 문제를 단계적으로 개선하는 포트폴리오 프로젝트다.

현재 v1에서는 샘플 문서 120개를 준비하고 Gemini Embedding API 연결을 검증했다. 구현 과정과 측정 결과는 [RAG 기준선 문서](./blogs/01-rag-baseline.md)에 기록한다.

## Embedding 연결 확인

Python 3.11 이상과 [uv](https://docs.astral.sh/uv/)가 필요하다.

```bash
uv sync --extra dev
cp .env.example .env
# .env에 GEMINI_API_KEY 입력
uv run python scripts/check_embedding.py
```

## 테스트

```bash
uv run pytest -q
uv run ruff check scripts/check_embedding.py src tests
```
