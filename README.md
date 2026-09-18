# LLM Wiki

가상 사내 문서로 RAG 검색의 기준선을 만들고, 검색 품질과 운영 문제를 단계적으로 개선하는 포트폴리오 프로젝트다.

현재는 Markdown 문서 준비, Gemini Embedding API 검증, 평가 질문 작성, 헤딩 기반 청킹까지 완료했다. 다음 단계에서 청크와 임베딩을 PostgreSQL + pgvector에 저장한다.

구현 의도와 검증 결과는 [RAG 기준선 문서](./blogs/01-rag-baseline.md)에 기록한다.

## 구현 현황

| 단계 | 상태 | 결과 |
| --- | --- | --- |
| 샘플 문서 준비 | 완료 | 4개 주제, 총 120개 Markdown 문서 |
| Embedding API 연결 | 완료 | `gemini-embedding-2`, 768차원 벡터 생성 확인 |
| 평가 질문 작성 | 완료 | 4개 시나리오, 총 20개 질문 |
| 청킹 | 완료 | 헤딩 단위 분할, 120개 문서 → 150개 청크 |
| pgvector 저장 | 다음 | 문서·청크·임베딩 저장 |
| 검색·평가 | 예정 | cosine similarity 검색, Hit@1·Hit@3 측정 |

## 프로젝트 구성

```text
sources/                    가상 Markdown 문서 120개
evaluation/questions.yaml   검색 평가 질문 20개
src/llm_wiki/documents.py   frontmatter와 본문 파싱
src/llm_wiki/chunking.py    Markdown 헤딩 기반 청킹
src/llm_wiki/embedding.py   Gemini 임베딩 클라이언트
scripts/                    임베딩·청킹 확인 스크립트
tests/                      문서·청킹·평가 데이터 테스트
```

## 실행 환경

Python 3.11 이상과 [uv](https://docs.astral.sh/uv/)가 필요하다.

```bash
uv sync --extra dev
cp .env.example .env
# .env에 GEMINI_API_KEY 입력
```

## Embedding 연결 확인

```bash
uv run python scripts/check_embedding.py
```

질문, 관련 문서, 무관한 문서를 각각 임베딩한 뒤 cosine similarity를 비교해 API 연결과 벡터 생성을 확인한다.

## 청킹 결과 확인

```bash
uv run python scripts/preview_chunks.py
uv run python scripts/preview_chunks.py --document-id deploy-guide-v22
```

Markdown 헤딩을 기준으로 본문을 의미 단위로 나눈다. 짧은 장애 리포트와 FAQ는 문서 전체가 하나의 청크로 유지된다.

현재 결과:

```text
documents=120
chunks=150
topic=deploy-guide documents=30 chunks=60
topic=error-codes documents=30 chunks=30
topic=incidents documents=30 chunks=30
topic=onboarding-faq documents=30 chunks=30
```

## 테스트

```bash
uv run pytest -q
uv run ruff check scripts/check_embedding.py scripts/preview_chunks.py src tests
```

테스트는 임베딩 입력 형식·차원 검증, 평가 질문 구조, frontmatter 파싱, 헤딩별 분할, 빈 청크 방지, 동일 입력의 결과 재현을 확인한다.
