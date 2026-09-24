# LLM Wiki

가상 사내 문서로 RAG 검색의 기준선을 만들고, 검색 품질과 운영 문제를 단계적으로 개선하는 포트폴리오 프로젝트다.

v1에서는 Markdown 문서 120개를 색인하고, 키워드·벡터 검색을 20개 질문으로 평가한 뒤 검색 결과에 출처를 붙여 답변한다. 다음 v2에서는 hybrid 검색과 최신 버전 필터를 적용한다.

구현 과정과 측정 결과는 [RAG 기준선 문서](./blogs/01-rag-baseline.md)에 기록한다.

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
| 키워드·벡터 검색 | 완료 | 문서 중복을 제거한 Top K 검색 |
| 검색 평가 | 완료 | 키워드 Hit@3 5%, 벡터 Hit@3 70% |
| 출처 기반 답변 | 완료 | 검색 청크만 사용한 답변·출처·근거 부족 응답 |
| API·MCP | 완료 | `health`, `search`, `answer` API와 MCP 도구 2개 |

## 프로젝트 구성

```text
sources/                    가상 Markdown 문서 120개
evaluation/questions.yaml   검색 평가 질문 20개
evaluation/results-v1.json  질문별 검색 순위와 Hit@K 결과
src/llm_wiki/documents.py   frontmatter와 본문 파싱
src/llm_wiki/chunking.py    Markdown 헤딩 기반 청킹
src/llm_wiki/embedding.py   Gemini 임베딩 클라이언트
src/llm_wiki/database.py    PostgreSQL 연결·스키마 초기화
src/llm_wiki/indexing.py    변경 감지·임베딩·트랜잭션 저장
src/llm_wiki/storage_validation.py  원본과 저장 결과 비교
src/llm_wiki/search.py      키워드·벡터 검색
src/llm_wiki/evaluation.py  Hit@1·Hit@3 평가
src/llm_wiki/answering.py   출처 기반 Gemini 답변
src/llm_wiki/api.py         FastAPI 엔드포인트
src/llm_wiki/mcp_server.py  API를 호출하는 MCP 도구
scripts/                    임베딩·청킹·DB·색인 스크립트
tests/                      문서·청킹·DB·색인·평가 데이터 테스트
```

## Docker Compose로 전체 실행

Docker Compose로 PostgreSQL, FastAPI, HTTP MCP 서버를 함께 실행한다. 최초 실행 전 `.env.example`을 `.env`로 복사하고 `GEMINI_API_KEY`를 입력한다. 기존 `.env`와 DB 데이터가 있으면 그대로 사용한다.

```bash
docker compose up -d --build
docker compose ps
```

기존에 tmux 등에서 로컬 API를 실행 중이라면 먼저 `Ctrl+C`로 종료해 8000 포트를 비운다. 첫 빌드 이후에는 `docker compose up -d`로 실행하고, 코드·의존성을 변경했으면 `--build`를 붙인다.

| 서비스 | 기본 접속 주소 | 역할 |
| --- | --- | --- |
| PostgreSQL | `127.0.0.1:5432` | 문서·청크·벡터 저장 |
| API | http://127.0.0.1:8000/docs | Swagger UI에서 검색·답변 실행 |
| MCP | `http://127.0.0.1:8001/mcp` | MCP 클라이언트용 Streamable HTTP 연결 |

DB의 healthcheck를 통과하면 API가 `init_db.py`로 없는 테이블·인덱스를 생성하고 시작한다. API의 `/health`가 정상 응답하면 MCP를 시작한다. 기존 데이터는 유지하며 `init_db.py`를 따로 실행할 필요가 없다. 스키마 초기화는 기존 테이블을 변경하는 마이그레이션이나 문서 색인을 수행하지 않는다.

**새 DB에 문서를 처음 넣을 때만** 아래 명령을 실행한다. 임베딩 API를 호출하며, 기존 데이터가 있다면 생략한다.

```bash
docker compose exec api python scripts/ingest.py
```

검색은 `/docs`에서 `POST /search` → **Try it out** → 다음 JSON 입력 → **Execute**로 확인한다. 답변과 출처는 같은 입력으로 `POST /answer`를 호출한다.

```json
{"query": "E-021 오류가 발생하면 어떤 설정을 확인해야 하나요?", "method": "vector", "top_k": 3}
```

컨테이너 내부에서는 API가 `postgres:5432`, MCP가 `http://api:8000`으로 연결한다. `.env`의 `DATABASE_URL`, `API_HOST`, `LLM_WIKI_API_URL`은 로컬 실행용이며 Compose 내부 주소는 별도로 설정한다. 호스트 공개 포트는 `.env`의 `POSTGRES_PORT`, `API_PORT`, `MCP_PORT`로 변경할 수 있다. `.env`는 이미지에 복사하지 않으며, Gemini 키는 API 컨테이너에만 전달한다.

```bash
docker compose logs -f api mcp
docker compose down
```

`down` 후에도 DB named volume의 데이터는 유지된다. 컨테이너의 문서는 빌드 시점의 `sources/` 사본이므로 문서를 수정한 뒤 컨테이너에서 색인하려면 먼저 `docker compose up -d --build`로 이미지를 갱신한다.

## 로컬 Python 실행 환경

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
docker compose up -d postgres
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

## 검색

```bash
uv run python scripts/search.py "E-021 오류 대응 방법" --method keyword
uv run python scripts/search.py "같은 서비스의 동시 배포를 어떻게 막나요?" --method vector
```

키워드 검색은 PostgreSQL `tsvector`, 벡터 검색은 pgvector cosine similarity를 사용한다. 두 방식 모두 같은 문서의 여러 청크 중 점수가 가장 높은 하나만 남긴 뒤 문서 Top K를 반환한다.

## 검색 평가

```bash
uv run python scripts/evaluate.py
```

고정 질문 20개를 두 검색 방식에 동일하게 실행한다. Hit@1은 단일 정답 질문 15개, Hit@3는 교차 문서 질문을 포함한 20개를 기준으로 계산한다. 질문별 검색 결과는 `evaluation/results-v1.json`에 저장된다.

| 검색 방식 | Hit@1 | Hit@3 |
| --- | ---: | ---: |
| 키워드 | 1/15 (6.7%) | 1/20 (5.0%) |
| 벡터 | 8/15 (53.3%) | 14/20 (70.0%) |

## 출처 기반 답변

```bash
uv run python scripts/answer.py "배포 가이드 v22에서 변경된 배포 금지 시간은 언제인가요?"
```

벡터 검색 Top 3를 Gemini에 전달하고, 근거 번호가 포함된 답변과 실제 출처 목록을 함께 출력한다. 검색 결과가 없으면 모델을 호출하지 않으며, 전달된 문서에 근거가 없으면 답변할 수 없다고 응답하도록 제한한다.

## API와 MCP

FastAPI가 실제 검색·답변 기능을 제공하고, MCP Server는 이 API를 Codex가 호출할 수 있는 도구로 노출한다.

```text
Codex → MCP Server → FastAPI → PostgreSQL + Gemini
```

### 1. Compose MCP를 Codex에 등록

전체 서비스를 Compose로 실행한 뒤 HTTP 주소를 등록한다.

```bash
codex mcp add llm-wiki --url http://127.0.0.1:8001/mcp
codex mcp get llm-wiki
```

기존 `llm-wiki` stdio 등록도 같은 이름의 위 명령으로 HTTP 등록으로 변경한다. `MCP_PORT`를 변경했다면 URL의 포트도 맞춘다. 등록 후 새 Codex 세션에서 사용한다. HTTP 서버 등록 방식은 [OpenAI 공식 문서](https://developers.openai.com/learn/docs-mcp)를 참고했다.

### 2. 로컬 API·stdio MCP로 실행하는 경우

Python 코드를 직접 실행하며 개발하려면 Compose의 API·MCP를 중지하고 로컬 API를 실행한다. 기존 색인 데이터가 있으면 색인 작업은 생략한다.

```bash
docker compose stop mcp api
docker compose up -d postgres
uv run python scripts/ingest.py  # 최초 색인 시에만 실행
uv run python scripts/serve_api.py
```

다른 터미널에서 저장소 루트를 기준으로 stdio MCP를 등록한다. `$(pwd)`는 현재 저장소의 절대 경로로 저장된다.

```bash
codex mcp add llm-wiki --env LLM_WIKI_API_URL=http://127.0.0.1:8000 -- uv run --directory "$(pwd)" python scripts/mcp_server.py
codex mcp get llm-wiki
```

이 로컬 실행 방식에서는 Codex가 MCP 프로세스를 시작한다. `scripts/mcp_server.py`의 기본값은 stdio이며, Compose에서는 `--transport streamable-http` 옵션으로 상시 HTTP 서버를 실행한다.

### 3. Codex에서 사용

MCP를 등록한 뒤 새 Codex 세션을 연다.

```bash
codex
```

Codex에 자연어로 요청한다.

```text
ask_wiki 도구를 사용해서 배포 가이드 v22에서 변경된
배포 금지 시간을 찾고 출처와 함께 답해줘.
```

Codex에서 검색 결과만 확인하려면 다음처럼 요청한다.

```text
search_wiki 도구로 E-021 오류 대응 문서를 3개 찾아줘.
```

호출 흐름은 다음과 같다.

```text
Codex
→ ask_wiki(query, method="vector", top_k=3)
→ POST /answer
→ 벡터 검색 Top 3 + Gemini 답변 생성
→ 답변과 출처 반환
```

| 구분 | 이름 | 역할 |
| --- | --- | --- |
| API | `GET /health` | DB 연결 확인 |
| API | `POST /search` | 키워드·벡터 검색 |
| API | `POST /answer` | 답변과 출처 반환 |
| MCP | `search_wiki` | `/search` 호출 |
| MCP | `ask_wiki` | `/answer` 호출 |

로컬 stdio 방식에서 API 주소를 변경했다면 MCP 등록 명령의 `LLM_WIKI_API_URL`도 같은 주소로 변경한다. Compose의 MCP는 내부 서비스 이름으로 API에 연결하므로 호스트 API 포트 변경의 영향을 받지 않는다.

## 테스트

```bash
uv run pytest -q
uv run ruff check scripts/check_embedding.py scripts/preview_chunks.py scripts/init_db.py scripts/ingest.py scripts/verify_storage.py scripts/search.py scripts/evaluate.py scripts/answer.py scripts/serve_api.py scripts/mcp_server.py src tests
```

테스트는 임베딩·청킹·색인·검색·Hit@K·출처 답변과 API·MCP 연결을 확인한다.
