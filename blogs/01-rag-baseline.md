# LLM Wiki v1 — RAG 기준선

> v1의 목표는 RAG를 잘 만드는 것이 아니다. **RAG가 어디까지 되고 어디서 안 되는지 측정**해서, 이후 LLM Wiki로 전환할 근거를 만드는 것이다.

이 글은 실험용 문서 준비부터 색인, 검색 평가, 출처 기반 답변까지 v1의 전체 과정을 다룬다.

## 문서는 어떻게 DB에 들어가나

### 일반적인 구성

실제 서비스에서는 문서가 계속 바뀐다. 그래서 변경된 문서만 비동기로 처리하고, 실패한 작업은 상태를 남긴 뒤 재시도하는 구성이 일반적이다.

![문서 변경부터 색인 완료까지의 비동기 파이프라인](https://raw.githubusercontent.com/hhk22/llm-wiki/docs/llm-wiki-devlog/images/indexing-pipeline-flow-v1.gif)

각 구성 요소의 역할은 다음과 같다.

| GIF 단계 | 구성 요소 | 하는 일 |
| --- | --- | --- |
| 2. 변경 감지 | 스케줄러 또는 웹훅 | 원본 저장소를 확인하고, 수정 시각·해시로 바뀐 문서만 골라낸다. |
| 3–4. 작업 큐·Worker | 큐 + Worker | 색인 요청을 분리해 처리하고, 파싱 → 청킹 → 임베딩 → 저장을 실행한다. |
| 5. 작업 상태 | 작업 상태 기록 | 성공·실패·재시도 상태를 남기고, 실패한 작업만 큐로 되돌린다. |

#### 새 커밋만 반영하기

```text
A 커밋 (9.14) → 색인 작업 1 → B 커밋 (9.15) → 색인 작업 2
```

색인 작업 2는 작업 1이 이미 반영한 `A`를 건너뛰고, `A` 이후 `B`까지 바뀐 파일만 처리한다. 마지막으로 반영한 커밋은 보통 DB의 상태 테이블에 `indexed_commit=A`처럼 저장해 둔다.

```text
git diff --name-status A B
  → 추가·수정·삭제된 파일만 작업 큐에 등록
```

작업이 모두 성공하면 `indexed_commit`을 `B`로 바꾼다. 실패하면 `A`를 유지해 다음 실행에서 같은 범위를 다시 확인한다.

### 이 프로젝트의 선택: 일괄 색인

v1에서는 위 구성을 만들지 않는다. **명령 한 번으로 문서 120개를 한 번에 색인한다.**

```text
sources/**/*.md → ingest → frontmatter 파싱 → 헤딩 기준 청킹 → 임베딩 API → PostgreSQL
```

문서 120개 규모에서는 일괄 색인으로 충분하다. 변경 감지, 작업 큐, 재시도는 이후 버전에서 단계적으로 추가한다. 다만 색인 입력의 해시를 함께 저장해 **바뀌지 않은 문서는 다시 임베딩하지 않는다.** 해시 계산 방법은 [6. 일괄 색인](#6-일괄-색인)에서 설명한다.

## 저장 구조

테이블은 두 개다.

| 테이블 | 저장하는 것 |
| --- | --- |
| `documents` | 문서 메타데이터(제목·주제·버전·수정일), 원본 파일 경로, 색인 입력 해시 |
| `chunks` | 청크 원문, 소속 문서와 섹션 경로, 임베딩 벡터, 키워드 색인 |

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
  id           text PRIMARY KEY,
  source_path  text NOT NULL UNIQUE,
  title        text NOT NULL,
  topic        text NOT NULL,
  version      text,
  updated_at   date,
  content_hash text NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
  document_id  text NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index  integer NOT NULL,
  heading_path text NOT NULL,        -- "배포 가이드 v22 > 현재 규칙"
  content      text NOT NULL,
  embedding    vector(768) NOT NULL, -- Gemini Embedding 2
  tsv          tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
  PRIMARY KEY (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING GIN (tsv);
```

- **임베딩은 외부 API로 만들고, pgvector는 저장과 거리 계산만 담당한다.** pgvector가 벡터를 만들어 주지는 않는다.
- **`tsv` 컬럼은 키워드 검색 기준선용이다.** 벡터 검색과 같은 조건에서 비교하기 위해 같은 테이블에 둔다.

## 구현 순서

```text
[완료]    1. 샘플 문서 준비
[완료]    2. Embedding API 연결
[완료]    3. 평가 질문 작성
[완료]    4. 청킹
[완료]    5. PostgreSQL + pgvector 스키마 구성
[완료]    6. 일괄 색인
[완료]    7. 저장 결과 검증
[완료]    8. 키워드·벡터 검색
[완료]    9. 검색 평가
[완료]   10. 출처 기반 답변
[완료]   11. API·MCP 연결
```

### 1. 샘플 문서 준비

`sources/`에 4개 주제, 주제별 30개씩 총 120개의 가상 Markdown 문서를 준비했다. 주제를 늘리는 대신 배포 가이드, 장애 리포트, 에러 코드, 온보딩 FAQ로 범위를 제한하고, 같은 형식 안에서 내용이 조금씩 달라지도록 구성했다.

| 주제 | 문서 | 구성 방식 |
| --- | --- | --- |
| 배포 가이드 | v1 ~ v30 | 버전마다 규칙 하나가 변경된다. |
| 장애 리포트 | #01 ~ #30 | 증상·원인·조치를 같은 형식으로 기록한다. |
| 에러 코드 | E-001 ~ E-030 | 의미·원인·대응 방법을 연결한다. |
| 온보딩 FAQ | Q01 ~ Q30 | 질문 하나에 답변 하나를 제공한다. |

문서는 짧게 유지하되, 버전 변화와 문서 간 연결을 검색으로 확인할 수 있게 만들었다. 예를 들어 배포 가이드 v22는 장애 리포트 #18을 계기로 배포 금지 시간이 바뀐 내용을 담는다. 모든 문서는 frontmatter에 id·제목·주제·수정일을 갖고, 문서 유형에 따라 버전·번호 등을 추가한다.

```markdown
---
id: deploy-guide-v22
title: 배포 가이드 v22
topic: deploy-guide
version: 22
updated_at: 2025-10-27
---

# 배포 가이드 v22

- 변경: 금요일 오후 대신 목요일 오후에 배포하지 않는다.
- 이유: 장애 리포트 #18 이후 결정

## 현재 규칙

- 배포 도구: GitHub Actions
- 배포 명령: `deploy run --env prod`
- 배포 금지 시간: 목요일 오후, 공휴일 전날
- 승인자: 팀 리드 + SRE 온콜
```

실제 문서에는 항목이 조금 더 있지만, 이 글에서는 위 예시로 설명을 이어간다. 전체 문서 목록은 [GitHub 저장소](https://github.com/hhk22/llm-wiki/tree/docs/llm-wiki-devlog)에서 확인할 수 있다.

### 2. Embedding API 연결

Gemini Embedding API로 텍스트를 768차원 벡터로 만든다.

| 설정 | 값 |
| --- | --- |
| 모델 | `gemini-embedding-2` |
| 출력 차원 | `768` |

#### 임베딩 테스트

검색 질문과 의미상 관련 있는 문서가 실제로 더 가까운 벡터로 표현되는지 확인했다.

```text
질문: "배포 금지 시간은 언제인가요?"
→ Embedding → Query Vector

문서 A: "목요일 오후와 공휴일 전날에는 프로덕션 배포를 하지 않는다."
→ Embedding → Vector A

문서 B: "로컬 개발 환경은 Docker Compose로 실행한다."
→ Embedding → Vector B
```

| 비교 | Cosine similarity |
| --- | ---: |
| 질문 ↔ 문서 A | `0.7833` |
| 질문 ↔ 문서 B | `0.5683` |

배포 질문과 관련 있는 문서 A의 유사도가 더 높았다. 검색 단계에서는 같은 방식으로 질문 벡터를 모든 청크 벡터와 비교해 유사도가 높은 순으로 문서를 고르고, 평가 질문의 Hit@K를 측정한다.

### 3. 평가 질문 작성

키워드 검색과 벡터 검색을 같은 조건에서 비교하기 위해, 실제 사용 상황을 네 가지 유형으로 나누고 유형별 5개씩 질문 20개와 정답 문서를 먼저 정했다. 전체 목록은 [`evaluation/questions.yaml`](../evaluation/questions.yaml)에 있다.

#### 정확한 키워드

> 질문: E-021 오류가 발생하면 어떤 설정을 확인해야 하나요?

에러 코드가 명시된 질문이다. `error-E-021`이 검색 결과에 포함되는지 Hit@1과 Hit@3로 확인한다.

#### 바꿔 말한 질문

> 질문: 같은 서비스에 두 배포가 겹쳐 올라가는 것을 어떻게 막나요?

문서의 "동시 배포 충돌"을 다른 표현으로 질문했다. 의미가 같은 `error-E-025`를 찾는지 확인한다.

#### 최신 정책

> 질문: 현재 프로덕션 배포 명령은 무엇인가요?

배포 가이드 v1부터 v30까지 비슷한 문서가 함께 존재한다. 이전 버전이 아니라 최신 규칙을 담은 `deploy-guide-v30`을 찾는지 확인한다.

#### 문서 간 연결

> 질문: 목요일 오후가 배포 금지 시간으로 바뀐 계기가 된 장애는 무엇인가요?

원인을 기록한 `incident-18`과 변경된 규칙을 기록한 `deploy-guide-v22`가 모두 Top 3에 포함되는지 확인한다.

### 4. 청킹

문서 전체를 하나의 벡터로 만들면 "변경 이유"와 "현재 규칙"처럼 성격이 다른 내용이 한 벡터에 섞인다. 이를 피하기 위해 frontmatter는 문서 정보로 따로 보관하고, 본문은 Markdown 헤딩을 기준으로 나눈다.

1번에서 본 배포 가이드 v22의 본문은 두 청크가 된다.

```text
Chunk 0
heading_path: 배포 가이드 v22
content:
  - 변경: 금요일 오후 대신 목요일 오후에 배포하지 않는다.
  - 이유: 장애 리포트 #18 이후 결정

Chunk 1
heading_path: 배포 가이드 v22 > 현재 규칙
content:
  - 배포 도구: GitHub Actions
  - 배포 명령: deploy run --env prod
  - 배포 금지 시간: 목요일 오후, 공휴일 전날
  - 승인자: 팀 리드 + SRE 온콜
```

변경 이유를 묻는 질문은 Chunk 0, 현재 규칙을 묻는 질문은 Chunk 1과 비교된다.

```text
"왜 목요일 오후에 배포할 수 없나요?" → Chunk 0
"현재 배포 승인자는 누구인가요?"     → Chunk 1
```

질문과 직접 관련된 부분만 검색되면 임베딩에 섞이는 주제가 줄고, LLM에 전달할 문맥과 출처도 정확해진다.

#### 너무 작게 나누면 생기는 문제

반대로 현재 규칙을 문장마다 나누면 하나의 의미 단위가 네 개의 청크로 흩어진다.

```text
Chunk A: "배포 도구: GitHub Actions"
Chunk B: "배포 명령: deploy run --env prod"
Chunk C: "배포 금지 시간: 목요일 오후, 공휴일 전날"
Chunk D: "승인자: 팀 리드 + SRE 온콜"
```

"현재 배포 규칙을 알려줘"라는 질문에 답하려면 네 청크를 다시 모아야 한다. Top 3만 검색하면 일부 규칙이 빠질 수 있고, `승인자`처럼 짧은 청크는 어떤 버전의 무슨 승인자인지 문맥도 부족하다.

따라서 문장 수나 글자 수로 잘게 자르지 않고, `현재 규칙`처럼 헤딩 아래 내용을 하나의 청크로 유지한다. 하위 헤딩이 없는 짧은 장애 리포트와 FAQ는 문서 전체가 하나의 청크가 된다.

#### 청킹 결과

120개 문서에 적용한 결과 150개 청크가 생성됐다. `## 현재 규칙` 헤딩이 있는 배포 가이드만 문서당 2개, 나머지는 문서당 1개다.

```text
배포 가이드 30개 → 60개 청크
에러 코드   30개 → 30개 청크
장애 리포트 30개 → 30개 청크
온보딩 FAQ  30개 → 30개 청크
```

### 5. PostgreSQL + pgvector 스키마 구성

| 구성 | 버전 |
| --- | --- |
| PostgreSQL | `17.11` |
| pgvector | `0.8.6` |

#### documents: 문서당 한 행

`documents`에는 Markdown 문서 하나당 한 행을 저장한다. 배포 가이드 v22의 frontmatter는 다음과 같이 들어간다.

```text
id:           deploy-guide-v22
source_path:  deploy-guide/v22.md
title:        배포 가이드 v22
topic:        deploy-guide
version:      22
updated_at:   2025-10-27
content_hash: 색인 입력의 SHA-256
```

`version`은 frontmatter의 `version: 22`에서 가져온 값이다. 배포 가이드 v1~v30을 구분하고 최신 규칙을 찾을 때 사용한다. 버전이 없는 장애 리포트·에러 코드·FAQ는 `NULL`로 둔다.

#### chunks: 검색 단위

`chunks`에는 4번에서 나눈 두 청크가 저장된다. `document_id`로 원본 문서를 참조하고, `chunk_index`로 문서 안의 순서를 구분한다.

```text
deploy-guide-v22 / chunk_index 0
└─ heading_path: 배포 가이드 v22

deploy-guide-v22 / chunk_index 1
└─ heading_path: 배포 가이드 v22 > 현재 규칙
```

`content`만 저장하면 PostgreSQL이 키워드 검색용 `tsv`를 자동으로 만든다. Python 쪽에서 `tsv`를 직접 만들지는 않는다.

```text
content: "배포 금지 시간: 목요일 오후, 공휴일 전날"

↓ to_tsvector('simple', content)

tsv: '공휴일':6 '금지':2 '목요일':4 '배포':1 '시간':3 '오후':5 '전날':7
```

숫자는 단어가 등장한 위치다. 검색 단계에서 `tsv`는 정확한 키워드 검색에, `embedding`은 의미가 비슷한 문장 검색에 쓴다.

### 6. 일괄 색인

`scripts/ingest.py`에서 전체 문서를 읽고, 새로 추가되었거나 바뀐 문서만 색인한다.

```text
Markdown 로딩
→ frontmatter 파싱
→ content_hash 계산
→ DB의 기존 해시와 비교
   ├─ 같음: 메타데이터만 갱신, 청킹·임베딩은 건너뜀
   └─ 없음·다름: 청킹 → 임베딩 → DB 저장
```

`content_hash`는 정규화한 `title`·`body`와 청킹 버전, 임베딩 모델·차원을 정렬한 뒤 SHA-256으로 계산한다. `topic`, `version`, `updated_at`은 청킹과 임베딩 입력에 사용하지 않으므로 해시에서 제외하고, 변경 시 `documents`의 메타데이터만 갱신한다.

배포 가이드 v22의 해시가 변경됐다면 두 청크를 다시 임베딩한다.

```text
배포 가이드 v22
├─ Chunk 0: 변경 내용·이유   → vector(768)
└─ Chunk 1: 현재 배포 규칙 → vector(768)
```

두 벡터가 모두 생성되면 하나의 트랜잭션으로 `documents`를 갱신하고 기존 `chunks`를 교체한다. 중간에 실패하면 이전 색인을 유지하고, `(document_id, chunk_index)` 기본 키로 반복 실행 시 중복을 방지한다.

초기 색인 중 Gemini API의 `503 UNAVAILABLE`을 겪었다. 문서별 트랜잭션과 `content_hash`로 완료된 문서는 건너뛰고 실패한 문서부터 이어서 처리했으며, `429`·`5xx` 오류에는 지수 백오프 재시도를 추가했다.

### 7. 저장 결과 검증

DB 건수만 세면 일부 문서가 다른 내용으로 저장된 경우를 찾을 수 없다. `verify_storage.py`에서 현재 Markdown을 다시 파싱·청킹하고, DB의 문서 ID·`content_hash`·청크 내용·벡터 차원과 비교했다.

```text
source / stored documents = 120 / 120
expected / stored chunks = 150 / 150
stored tsv = 150
embedding dimensions (min/max) = 768 / 768
content hash mismatches = 0
missing / unexpected chunks = 0 / 0
chunk content mismatches = 0
duplicate chunks = 0
status = PASS
```

같은 입력으로 다시 실행한 결과 120개 문서를 모두 건너뛰고 DB의 문서·청크 수도 유지됐다.

```text
indexed_documents = 0
skipped_documents = 120
indexed_chunks = 0
```

### 8. 키워드·벡터 검색

키워드 검색은 문서 제목·헤딩·본문의 `tsvector`를 `ts_rank_cd`로 정렬한다. 벡터 검색은 질문을 768차원으로 임베딩하고 pgvector의 cosine similarity로 가까운 청크를 찾는다.

```sql
1 - (embedding <=> query_vector) AS cosine_similarity
```

같은 문서의 청크가 Top 3를 모두 차지하지 않도록, 문서별 최고 점수 청크 하나만 남긴 뒤 문서 Top 3를 반환한다.

### 9. 검색 평가

3번에서 정한 질문 20개를 두 검색 방식에 동일하게 실행했다. 단일 문서 질문 15개는 Hit@1·Hit@3, 교차 문서 질문 5개는 필요한 두 문서가 모두 Top 3에 있는지 확인했다.

| 검색 방식 | Hit@1 | Hit@3 |
| --- | ---: | ---: |
| 키워드 | 1/15 (6.7%) | 1/20 (5.0%) |
| 벡터 | 8/15 (53.3%) | 14/20 (70.0%) |

| 질문 유형 | 키워드 Hit@3 | 벡터 Hit@3 |
| --- | ---: | ---: |
| 정확한 키워드 | 20% | 100% |
| 바꿔 말한 질문 | 0% | 100% |
| 최신 정책 | 0% | 0% |
| 문서 간 연결 | 0% | 80% |

벡터 검색은 표현이 달라진 질문과 문서 간 연결에 강했지만, 비슷한 배포 가이드 v1~v30 중 최신 문서를 고르지 못했다. 질문별 Top 3는 [`evaluation/results-v1.json`](../evaluation/results-v1.json)에 기록했다.

```text
질문: 현재 프로덕션 배포 명령은 무엇인가요?
기대: deploy-guide-v30
벡터 Top 3: faq-q01, deploy-guide-v15, deploy-guide-v21
```

### 10. 출처 기반 답변

벡터 검색 Top 3를 `gemini-3.6-flash`에 전달하고, 제공된 문서만 사용해 근거 번호를 표시하도록 했다.

```text
질문: 배포 가이드 v22에서 변경된 배포 금지 시간은 언제인가요?

답변: 금요일 오후 대신 목요일 오후이며,
      공휴일 전날은 유지됩니다. [1]

[1] 배포 가이드 v22 (deploy-guide-v22, chunk 0)
```

문서에 없는 질문에는 `근거 문서에서 확인할 수 없습니다.`라고 응답하는 것도 확인했다. 다만 최신 문서가 검색되지 않으면 답변 모델이 이를 고칠 수 없으므로, v2에서는 hybrid 검색과 최신 버전 필터를 같은 질문으로 다시 평가한다.

### 11. API·MCP 연결

기존 검색·답변 로직을 FastAPI로 감싸고, MCP는 같은 로직을 다시 구현하지 않고 HTTP API를 호출한다.

```text
MCP Host → search_wiki / ask_wiki → HTTP API → 검색·답변 파이프라인
```

API는 `/health`, `/search`, `/answer` 세 개만 두고, MCP는 검색과 질문 도구 두 개만 제공한다. 실제 MCP 호출에서 `ask_wiki`가 답변과 `deploy-guide-v22` 출처를 반환하는 것까지 확인했다.
