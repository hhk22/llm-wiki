# LLM Wiki

가상 사내 문서로 RAG 검색의 기준선을 만들고, 검색 품질과 운영 문제를 단계적으로 개선하는 포트폴리오 프로젝트다.

현재는 Markdown 문서 120개를 150개 청크로 나눈 뒤 Gemini 임베딩을 생성해 PostgreSQL + pgvector에 일괄 색인했다. 다음 단계에서 키워드 검색과 벡터 검색을 평가한다.

구현 의도와 검증 결과는 [RAG 기준선 문서](./blogs/01-rag-baseline.md)에 기록한다.

## 구현 현황

| 단계 | 상태 | 결과 |
| --- | --- | --- |
| 샘플 문서 준비 | 완료 | 4개 주제, 총 120개 Markdown 문서 |
| Embedding API 연결 | 완료 | `gemini-embedding-2`, 768차원 벡터 생성 확인 |
| 평가 질문 작성 | 완료 | 4개 시나리오, 총 20개 질문 |
| 청킹 | 완료 | 헤딩 단위 분할, 120개 문서 → 150개 청크 |
| PostgreSQL + pgvector 실행 + DB 연결 및 스키마 구성 | 완료 | PostgreSQL 17.11, pgvector 0.8.6, `vector(768)` |
| 일괄 색인 | 완료 | 문서 120개, 청크 150개, 768차원 벡터 저장 |
| 저장 결과 검증 | 완료 | 원본 대비 누락·중복·내용 불일치 0건 |
| 검색·평가 | 다음 | cosine similarity 검색, Hit@1·Hit@3 측정 |

## 프로젝트 구성

```text
sources/                    가상 Markdown 문서 120개
evaluation/questions.yaml   검색 평가 질문 20개
src/llm_wiki/documents.py   frontmatter와 본문 파싱
src/llm_wiki/chunking.py    Markdown 헤딩 기반 청킹
src/llm_wiki/embedding.py   Gemini 임베딩 클라이언트
src/llm_wiki/database.py    PostgreSQL 연결·스키마 초기화
src/llm_wiki/indexing.py    변경 감지·임베딩·트랜잭션 저장
src/llm_wiki/storage_validation.py  원본과 저장 결과 비교
scripts/                    임베딩·청킹·DB·색인 스크립트
tests/                      문서·청킹·DB·색인·평가 데이터 테스트
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

## PostgreSQL + pgvector 실행·스키마 구성

```bash
docker compose up -d
docker compose ps
uv run python scripts/init_db.py
```

PostgreSQL은 `127.0.0.1:5432`에서 실행되며, 첫 초기화 시 `vector` 확장을 자동으로 활성화한다. `init_db.py`는 `.env`의 `DATABASE_URL`로 연결해 `documents`, `chunks`, 키워드 검색용 GIN 색인을 생성한다. 데이터는 Docker named volume에 유지된다.

```bash
docker compose down
```

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

## 일괄 색인

```bash
uv run python scripts/ingest.py
```

`content_hash`가 같은 문서는 건너뛰고, 새로 추가되었거나 바뀐 문서만 청킹·임베딩해 저장한다. `429`와 `5xx` API 오류는 지수 백오프로 재시도한다.

```text
stored_documents=120
stored_chunks=150
invalid_embedding_dimensions=0

# 같은 입력으로 재실행
indexed_documents=0
skipped_documents=120
indexed_chunks=0
```

## 저장 결과 검증

```bash
uv run python scripts/verify_storage.py
```

현재 Markdown을 다시 파싱·청킹한 결과와 DB의 문서 ID, 해시, 청크 내용, 벡터 차원을 비교한다. 검증 실패 시 종료 코드 `1`을 반환한다.

```text
source_documents=120
stored_documents=120
expected_chunks=150
stored_chunks=150
stored_tsv=150
embedding_dimensions=768/768
content_hash_mismatches=0
missing_chunks=0
unexpected_chunks=0
chunk_content_mismatches=0
duplicate_chunks=0
status=PASS
```

## 테스트

```bash
uv run pytest -q
uv run ruff check scripts/check_embedding.py scripts/preview_chunks.py scripts/init_db.py scripts/ingest.py scripts/verify_storage.py src tests
```

테스트는 임베딩 입력·오류 처리, frontmatter 파싱, 헤딩별 분할, DB 스키마, 변경 감지 해시, API 재시도, 저장 결과 검증, 평가 질문 구조를 확인한다.
