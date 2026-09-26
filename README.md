# LLM Wiki

가상 사내 문서로 RAG 검색의 기준선을 만들고, 검색 품질과 운영 문제를 단계적으로 개선하는 포트폴리오 프로젝트다.

v1에서는 Markdown 문서 120개를 색인하고, 키워드·벡터 검색을 20개 질문으로 평가한 뒤 검색 결과에 출처를 붙여 답변한다. v2에서는 오류 코드 조사 보정과 hybrid 검색을 구현했다. 고정 20문항에서 hybrid Hit@3는 55%로 벡터의 70%보다 낮아 기본 검색은 벡터로 유지한다. 3단계 최신 버전 필터를 적용한 벡터의 문서 Hit@3는 95%(19/20)이며 기존 성공 문항은 유지됐다. 4단계에서는 답변에 문서당 최대 2개 청크를 전달해, 대표 청크 하나를 고를 때 빠지던 근거를 함께 제공한다.

구현 과정과 측정 결과는 [RAG 기준선](./blogs/01-rag-baseline.md)과 [v2 검색 품질 실험](./blogs/02-search-quality.md)에 기록한다.

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
| v1 검색 평가 | 완료 | 키워드 Hit@3 5%, 벡터 Hit@3 70% |
| v2 조사 보정·Hybrid | 구현·평가 완료 | 보정 키워드 Hit@3 10%, hybrid 55%; 기본값은 벡터 유지 |
| v2 최신 버전 필터 | 구현·평가 완료 | 벡터 + 필터 문서 Hit@3 95%, hybrid + 필터 75% |
| 출처 기반 답변 | 완료 | 검색 청크만 사용한 답변·출처·근거 부족 응답 |
| API·MCP | 완료 | `health`, `search`, `answer` API와 MCP 도구 2개 |
| v3 Wiki 생성 | 초기 구축 완료 | 본문 120개·목차 5개, 관련 링크·원본 출처 생성; Wiki 질의는 후속 구현 |
| v3 시점·변경 이력 | 구현·검증 완료 | 과거 표현 보정, 규칙 항목 변경 14건 연결, 배포 정책 상충 후보 검토 |

## 프로젝트 구성

```text
sources/                    가상 Markdown 문서 120개
evaluation/questions.yaml   검색 평가 질문 20개
evaluation/results-v1.json  v1 검색 기준선
evaluation/results-v2-hybrid.json  hybrid 후보·근거·전후 비교 결과
src/llm_wiki/documents.py   frontmatter와 본문 파싱
src/llm_wiki/chunking.py    Markdown 헤딩 기반 청킹
src/llm_wiki/embedding.py   Gemini 임베딩 클라이언트
src/llm_wiki/database.py    PostgreSQL 연결·스키마 초기화
src/llm_wiki/indexing.py    변경 감지·임베딩·트랜잭션 저장
src/llm_wiki/storage_validation.py  원본과 저장 결과 비교
src/llm_wiki/search.py      키워드·벡터·RRF hybrid 검색
src/llm_wiki/evaluation.py  Hit@1·Hit@3 평가
src/llm_wiki/answering.py   출처 기반 Gemini 답변
src/llm_wiki/api.py         FastAPI 엔드포인트
src/llm_wiki/mcp_server.py  API를 호출하는 MCP 도구
scripts/                    임베딩·청킹·DB·색인 스크립트
tests/                      문서·청킹·DB·색인·평가 데이터 테스트
```

## v3 — 원본으로 Wiki 생성

`sources/`를 읽어 Gemini가 배포 버전·장애 사건·오류 코드·FAQ별 설명을 종합하고, 관련 링크와 주제별·전체 목차를 생성한다. 기존 `vector`·`hybrid`·`keyword` 검색과 답변 기능은 그대로 유지한다. Wiki를 읽어 답하는 API·MCP 경로는 아직 구현하지 않았다.

`gemini-3.5-flash-lite`로 본문 120개·목차 5개를 구축했다. [Wiki 목차](wiki/index.md), [배포 v22 종합 예시](wiki/deployments/v22.md), [초기 생성 기록](evaluation/wiki-build-review.md)에서 확인할 수 있다. 시점·이력 보강 후 링크 2,166개와 기존 본문 근거 인용 978개가 검증을 통과했다. 이력은 원본에서 다시 계산해 출력과 대조하며, 전체 테스트는 DB 연동을 포함해 146개 통과했다.

`.env`에 `GEMINI_API_KEY`와 `GEMINI_GENERATION_MODEL`을 설정한 뒤 실행한다. 이 단계는 DB나 임베딩을 사용하지 않는다.

```bash
uv run python scripts/build_wiki.py --model gemini-3.5-flash-lite --output wiki-rebuild
uv run python scripts/verify_wiki.py --wiki wiki-rebuild
# 저장소에 포함된 Wiki 검증 (API 호출 없음)
uv run python scripts/verify_wiki.py
```

중단되면 같은 원본·모델·배치 크기로 `--resume`을 사용한다. 완료된 LLM 호출은 재사용하고, 결과를 다시 검증한다. 원본이나 생성 설정을 바꾼 전체 재생성은 새 출력 폴더를 지정한다.

```bash
uv run python scripts/build_wiki.py --resume --model gemini-3.5-flash-lite --output wiki-rebuild
```

Wiki 생성 모델만 바꾸려면 `--model`을 지정한다. 기존 검색 답변의 모델 설정은 바꾸지 않는다. `--resume`에도 최초 생성 때의 모델을 동일하게 지정해야 한다.

`--resume`은 생성 당시의 원본·코드·설정을 그대로 유지한 작업을 이어갈 때 사용한다. 오프라인 검증은 Git 체크아웃의 LF/CRLF 차이를 허용하지만, 재개는 원본과 생성 코드의 바이트 해시까지 일치해야 한다.

생성기 코드만 수정한 작업은 `--resume --accept-code-change`로 이어갈 수 있다. 이전 코드 해시와 재사용할 작업 목록을 기록하고, 기존 결과를 현재 검증기로 다시 검사한다. 코드 변경으로 개별 작업의 입력 프롬프트가 달라졌다면 이전 결과를 `superseded_jobs`에 보존하고 그 작업만 다시 호출한다. 원본·모델·기본 페이지 작성 정책·기본 응답 스키마 변경은 이 옵션으로 허용하지 않는다. 별도 상충 검토 정책과 스키마는 `wiki_history.py` 코드 해시 및 호출별 스키마에 기록한다.

| 생성물 | 내용 |
| --- | --- |
| `wiki/index.md`, 각 주제의 `index.md` | LLM이 요약을 바탕으로 구성한 목차 |
| `wiki/deployments/v*.md` | 버전별 규칙·변경 이유·원본 출처 |
| `wiki/incidents/*.md`, `errors/*.md`, `onboarding/*.md` | 사건·코드·FAQ별 종합 설명과 관련 링크 |
| `wiki/_build/manifest.json` | 원본 해시·모델·설정·호출 시간·토큰·검증된 구조화 결과·파일 해시 |
| `wiki/_build/calls/` | 호출별 프롬프트·출력 스키마·원본 응답 |

생성은 페이지 작성 → 관련 링크 선택 → 목차 구성 순서다. 기본적으로 5개 페이지씩 요청하며, 대상 원본과 명시적 참조로 연결된 원본을 함께 전달한다(들어오는 참조 1단계, 나가는 참조 2단계). 관련 링크와 목차는 생성된 전체 페이지의 요약을 바탕으로 작성한다. 평가 질문·정답은 입력하지 않는다. 형식·출처 검증 실패와 일시적인 API 오류는 작업당 최대 5회 시도하며, 발견한 오류를 누적해 재생성에 전달한다. 미완료 상태는 manifest에 남기며 재개할 때도 이전 오류를 전달한다.

검증은 원본 인용의 존재, 핵심 원문 줄의 인용 누락, 명시된 장애 원본의 근거 포함, 페이지·목차 누락, 링크 대상, 출처 행 번호, 원본·생성 파일 해시를 확인한다. **인용이 존재한다고 종합한 주장이 정확하거나 빠짐없다는 뜻은 아니다.** 의미·인과관계·시점은 생성물과 원본을 대조해 별도로 검토한다.

시점·이력 보강은 `src/llm_wiki/wiki_history.py`에서 처리한다. 배포 가이드의 연속 버전에서 `현재 규칙` 항목의 값을 비교하고, 각 페이지의 버전까지 확인된 이력만 원본 출처와 연결한다. 과거 페이지의 `현재 규칙`·`현재 배포` 표현에는 해당 버전을 표시하고 최신 가이드 링크를 붙인다. 이유 항목에서 명시한 장애만 원인 사건으로 연결한다.

목차 생성 후 배포 가이드와 이를 참조하는 원본의 본문·버전·변경 정보를 LLM에 전달해 상충 후보를 검토한다. 서로 다른 가이드 버전만 비교한 결과는 충돌로 채택하지 않는다. 후보는 `확인 필요` 절에 양쪽 주장·원본 출처·시점 불확실성을 표시하고 자동으로 정답을 선택하지 않는다. 한 원본의 동일 규칙에 서로 다른 값이 중복된 경우도 코드로 표시한다. [보강 결과와 한계](evaluation/wiki-history-review.md)에 실제 원본 및 인위적 상충 사례의 검증을 기록했다.

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

`/search`, `/answer`, MCP의 `search_wiki`·`ask_wiki`는 모두 `method`에 `keyword`, `vector`, `hybrid`를 지원한다. 기본값은 `vector`이며, `hybrid`는 비교용 옵션이다. 현재 배포 정책 질문에는 최신 버전 필터가 자동 적용되고, 특정 버전은 해당 버전으로 검색한다. 변경 이력·비교·불분명한 질문은 범위를 제한하지 않는다.

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
uv run python scripts/search.py "E-021 오류가 발생하면 어떤 설정을 확인해야 하나요?" --method hybrid
```

키워드 검색은 PostgreSQL `tsvector`, 벡터 검색은 pgvector cosine similarity를 사용한다. 검색 목록은 문서별 최고 점수 청크로 문서 Top K를 반환한다. 답변 생성은 같은 문서 순위를 유지하며 문서당 상위 청크를 최대 2개 전달한다. 키워드 검색에는 오류 코드의 조사 제거가 적용된다. Hybrid는 양쪽 문서 Top 10을 RRF(상수 60, 키워드 1 : 벡터 3)로 결합하고, 동점은 문서 ID순으로 정렬한다. 두 후보에 있는 문서는 벡터 검색의 청크를 사용하며, hybrid 응답의 `score`는 RRF 점수다. CLI의 `--top-k`가 10보다 크면 양쪽 후보 수도 함께 늘린다.

현재 정책 여부는 명시적인 한국어 표현으로 판단한다. 예를 들어 `현재 프로덕션 배포 명령은 무엇인가요?`는 숫자 버전이 가장 큰 배포 가이드만 검색하며, `배포 가이드 v22의 금지 시간은?`는 v22를 검색한다. 최신 버전 번호는 DB에서 계산한다. `현재 배포 규칙이 바뀐 계기는?`는 과거 가이드와 장애 문서를 유지한다. 키워드·벡터·hybrid 및 API·MCP·CLI에 같은 조건이 적용되며 재색인은 필요 없다.

## 검색 평가

```bash
uv run python scripts/evaluate.py
```

2단계 재현용으로 버전 필터를 끄고, 고정 질문 20개로 보정 전·후 키워드, 벡터, 보정 전·후 hybrid 다섯 방식을 비교한다. 질문당 임베딩과 벡터 후보를 공유해 순위 결합 효과를 비교한다. Hit@1은 단일 정답 15문항, Hit@3는 교차 문서 질문을 포함한 20문항 기준이다. 후보·대표 청크·점수·단계별 시간·퇴보 문항은 `evaluation/results-v2-hybrid.json`에 저장하며, `--output`으로 출력 경로를 바꿀 수 있다. 기존 `results-v1.json` 덮어쓰기는 차단한다.

| 검색 방식 | Hit@1 | Hit@3 |
| --- | ---: | ---: |
| 보정 전 키워드 | 1/15 (6.7%) | 1/20 (5.0%) |
| 조사 보정 키워드 | 2/15 (13.3%) | 2/20 (10.0%) |
| 벡터 | 8/15 (53.3%) | 14/20 (70.0%) |
| 보정 전 키워드 + 벡터 RRF | 4/15 (26.7%) | 11/20 (55.0%) |
| 조사 보정 키워드 + 벡터 RRF | 5/15 (33.3%) | 11/20 (55.0%) |

### 3단계 버전 필터 평가

```bash
uv run python scripts/evaluate_filters.py
uv run python scripts/evaluate_filters.py --questions evaluation/filter-validation.yaml --output evaluation/results-v2-filters-validation.json
```

같은 질문 임베딩으로 keyword·vector·hybrid의 필터 전후 6개 변형을 비교한다. 고정 20문항 결과는 `evaluation/results-v2-filters.json`에, 별도 표현 6문항은 별도 파일에 저장한다. 문서 Hit@3는 벡터 14/20 → 19/20, hybrid 11/20 → 15/20이며 각각 기존 성공 문항의 퇴보는 없다. 최신 정책 5문항 중 실제 답을 포함한 청크는 4개로, 문서 적중과 근거 확보를 구분한다.

### 4단계 근거 청크 수 비교

```bash
uv run python scripts/evaluate_evidence.py
```

고정 20문항에서 문서당 청크 1개·2개의 문서 순위와 기존 근거 보존 여부를 비교한다. 세 검색 방식 모두 문서 순위와 기존 대표 청크가 유지됐다. 별도로 최신 정책 5문항과 기존 성공 3문항을 답변 비교 대상으로 정했다. 같은 모델·프롬프트로 비교한 8문항의 전후 답변은 `evaluation/results-v2-evidence.json`에 저장했다. 문서 Hit@3와 실제 답변 내용은 구분해 확인한다. 티켓 질문은 근거 부족 응답에서 `--ticket <id>`를 인용하는 답변으로 바뀌었다. 호출 제한에 맞춰 생성 요청 간격을 16초 이상 두고 중간 결과를 저장하며, 중단 시 `--resume`으로 이어갈 수 있다.

### 사례 2·3: 원인 장애 참조와 가중 RRF

```bash
uv run python scripts/evaluate_followups.py
uv run python scripts/check_followup_answers.py
```

첫 명령은 저장된 20문항의 검색 후보와 현재 DB로 참조 보강·가중치 효과를 분리 평가하며 Gemini를 호출하지 않는다. 두 번째 명령은 5개 실패 사례의 질문 임베딩과 답변을 실제 호출해 전후 비교한다. 생성 호출은 16초 간격으로 실행하고 `--resume`으로 중간 저장 결과부터 이어갈 수 있다.

정책 변경 이유·계기를 묻는 질문은 검색된 최상위 가이드의 `이유:` 줄에서 명시한 장애 문서를 한 번 조회해 가이드 바로 뒤에 넣는다. 결과는 여전히 문서 Top K 이내이며, 없는 참조는 건너뛴다. 이 방식으로 추가된 근거는 `reference_from`에 가이드 ID를 담고, 검색 점수가 아닌 참조 조회이므로 `score=0`을 사용한다. 일반 검색 결과의 `reference_from`은 `null`이다. 참조로 추가하는 장애 문서의 청크는 원문 순서로 최대 2개를 가져온다.

저장 후보 재평가에서 벡터 + 참조는 문서 Hit@3 19/20 → 20/20이었다. 참조를 제외한 가중 hybrid는 15/20 → 19/20으로 올랐지만, E-021의 1위 퇴보로 Hit@1은 11/15 → 10/15였다. 기본 검색은 벡터이며, 이 결과는 개발용 질문 세트의 검색 평가다.

## 출처 기반 답변

```bash
uv run python scripts/answer.py "배포 가이드 v22에서 변경된 배포 금지 시간은 언제인가요?"
```

기본값은 벡터 검색이며 `--method hybrid`로 비교할 수 있다. 검색한 문서 Top 3에서 문서당 상위 청크를 최대 2개씩 Gemini에 전달하고, 하나의 답변과 청크별 근거 번호를 출력한다. `/search`·`search_wiki`는 문서당 대표 청크 1개, `/answer`·`ask_wiki`는 문서당 최대 2개를 반환한다. `top_k`는 문서 수이므로 기본 답변의 `sources`는 최대 6개다. CLI에서 `--chunks-per-document 1`을 지정하면 이전 방식과 비교할 수 있다. 검색 결과가 없으면 모델을 호출하지 않으며, 전달된 문서에 근거가 없으면 답변할 수 없다고 응답하도록 제한한다.

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
→ 벡터 검색 문서 Top 3 → 문서당 최대 2개 청크 + Gemini 답변 생성
→ 답변과 출처 반환
```

| 구분 | 이름 | 역할 |
| --- | --- | --- |
| API | `GET /health` | DB 연결 확인 |
| API | `POST /search` | 키워드·벡터·hybrid 검색 |
| API | `POST /answer` | 답변과 출처 반환 |
| MCP | `search_wiki` | `/search` 호출 |
| MCP | `ask_wiki` | `/answer` 호출 |

로컬 stdio 방식에서 API 주소를 변경했다면 MCP 등록 명령의 `LLM_WIKI_API_URL`도 같은 주소로 변경한다. Compose의 MCP는 내부 서비스 이름으로 API에 연결하므로 호스트 API 포트 변경의 영향을 받지 않는다.

## 테스트

```bash
uv run pytest -q
uv run ruff check scripts/check_embedding.py scripts/preview_chunks.py scripts/init_db.py scripts/ingest.py scripts/verify_storage.py scripts/search.py scripts/evaluate.py scripts/evaluate_filters.py scripts/evaluate_evidence.py scripts/evaluate_followups.py scripts/check_followup_answers.py scripts/answer.py scripts/serve_api.py scripts/mcp_server.py src tests
```

테스트는 임베딩·청킹·색인·검색·Hit@K·출처 답변과 API·MCP 연결을 확인한다.

버전 필터의 실제 PostgreSQL 테스트는 `TEST_DATABASE_URL`을 설정하면 함께 실행된다. 연결 안에서만 보이는 임시 테이블을 사용해 기존 문서를 변경하지 않으며, v31·v100 추가와 숫자 버전 정렬, 특정 버전 및 이력 검색을 검증한다.

```bash
TEST_DATABASE_URL=postgresql://llm_wiki:llm_wiki@localhost:5432/llm_wiki uv run pytest -q tests/test_search_scope.py
```
