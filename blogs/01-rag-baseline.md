# LLM Wiki v1 — RAG 기준선

> v1의 목표는 검색 성능을 최대한 높이는 것이 아니다. **현재 구조가 어떤 질문을 맞히고 어디서 실패하는지 측정해 다음 개선의 기준을 만드는 것**이다.

가상 사내 문서 120개를 PostgreSQL + pgvector에 색인하고, 키워드 검색과 벡터 검색을 같은 질문 20개로 비교했다. 검색 결과는 Gemini에 전달해 출처 기반 답변으로 연결했다.

## 결과 먼저 보기

| 항목 | 결과 |
| --- | --- |
| 원본 문서 | 4개 주제, Markdown 120개 |
| 검색 단위 | 헤딩 기반 청크 150개 |
| 임베딩 | `gemini-embedding-2`, 768차원 |
| 저장소 | PostgreSQL 17.11, pgvector 0.8.6 |
| 키워드 검색 | Hit@1 `6.7%`, Hit@3 `5.0%` |
| 벡터 검색 | Hit@1 `53.3%`, Hit@3 `70.0%` |
| 가장 큰 한계 | 최신 정책 질문 Hit@3 `0%` |

벡터 검색은 바꿔 말한 질문을 모두 Top 3 안에서 찾았지만, 비슷한 버전 문서 중 최신 문서를 고르지 못했다. 이 결과를 v2의 hybrid 검색과 최신 버전 필터를 적용할 근거로 삼는다.

## 구현 범위

```text
Markdown 120개
→ 파싱·청킹
→ Gemini 임베딩
→ PostgreSQL + pgvector 저장
→ 키워드·벡터 검색
→ Hit@K 평가
→ 출처 기반 답변
→ FastAPI·MCP 제공
```

## 문서는 어떻게 DB에 들어가나

실제 서비스에서는 문서 변경을 감지해 작업 큐에 넣고, Worker가 비동기로 색인하는 구성이 일반적이다. 성공·실패 상태를 남겨 실패한 작업만 다시 처리한다.

![문서 변경부터 색인 완료까지의 비동기 파이프라인](https://raw.githubusercontent.com/hhk22/llm-wiki/docs/llm-wiki-devlog/images/indexing-pipeline-flow-v1.gif)

```text
원본 변경 → 변경 감지 → 작업 큐 → Worker → 파싱·청킹·임베딩 → DB
                              └→ 성공·실패·재시도 상태 기록
```

v1은 문서가 120개이므로 큐와 Worker 대신 명령 한 번으로 전체 파일을 확인하는 일괄 색인을 선택했다.

```text
sources/**/*.md → ingest → 해시 비교 → 파싱·청킹·임베딩 → PostgreSQL
                                  └→ 변경 없는 문서는 건너뜀
```

| 구분 | 일반적인 구성 | v1 |
| --- | --- | --- |
| 변경 확인 | 스케줄러·웹훅 | 실행 시 전체 파일의 해시 비교 |
| 처리 | 작업 큐 + Worker | 단일 프로세스 일괄 색인 |
| 실패 복구 | 작업 상태를 기록해 재시도 | 문서별 트랜잭션 후 재실행 |

구성은 단순하게 유지하되, `content_hash`로 변경 없는 문서를 건너뛰고 API 오류를 재시도해 반복 실행 비용과 중간 실패를 줄였다.

## 1. 실험용 문서와 평가 질문

### 문서 구성

[`sources/`](../sources)에 주제별 30개씩 총 120개의 가상 문서를 만들었다. 문서 종류를 늘리기보다 버전 변화, 표현 차이, 문서 간 연결을 검색으로 확인할 수 있게 구성했다.

| 주제 | 문서 | 확인하려는 문제 |
| --- | ---: | --- |
| 배포 가이드 | v1~v30 | 비슷한 문서 사이에서 최신 버전을 찾는가 |
| 장애 리포트 | #01~#30 | 증상·원인·조치를 연결하는가 |
| 에러 코드 | E-001~E-030 | 정확한 식별자와 대응 방법을 찾는가 |
| 온보딩 FAQ | Q01~Q30 | 같은 의미의 다른 표현을 찾는가 |

예를 들어 배포 가이드 v22는 장애 리포트 #18 이후 배포 금지 시간이 바뀐 내용을 담는다.

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

- 배포 명령: `deploy run --env prod`
- 배포 금지 시간: 목요일 오후, 공휴일 전날
```

### 평가 질문

검색 결과를 본 뒤 질문을 고르지 않도록 구현 전에 질문 20개와 정답 문서를 고정했다.

| 유형 | 예시 | 성공 기준 |
| --- | --- | --- |
| 정확한 키워드 | `E-021 오류가 발생하면?` | 정답 문서가 Top 1·3에 포함 |
| 바꿔 말한 질문 | `두 배포가 겹치는 것을 어떻게 막나요?` | 의미가 같은 문서가 Top 1·3에 포함 |
| 최신 정책 | `현재 프로덕션 배포 명령은?` | `deploy-guide-v30`이 Top 1·3에 포함 |
| 문서 간 연결 | `목요일 배포 금지의 계기가 된 장애는?` | 필요한 두 문서가 모두 Top 3에 포함 |

질문과 정답은 [`evaluation/questions.yaml`](../evaluation/questions.yaml)에 기록했다.

## 2. 문서를 검색 가능한 형태로 저장하기

### 헤딩 기반 청킹

문서 전체를 하나의 벡터로 만들면 변경 이유와 현재 규칙이 섞인다. 반대로 문장마다 나누면 문맥이 사라지고 검색 대상이 불필요하게 늘어난다. 그래서 Markdown 헤딩 아래 내용을 하나의 의미 단위로 유지했다.

```text
배포 가이드 v22
├─ Chunk 0: 변경 내용·이유
└─ Chunk 1: 현재 배포 규칙
```

질문별로 필요한 청크가 달라진다.

```text
"왜 목요일 오후에 배포할 수 없나요?" → Chunk 0
"현재 배포 금지 시간은 언제인가요?" → Chunk 1
```

적용 결과 배포 가이드 30개는 60개 청크가 됐고, 짧은 장애 리포트·에러 코드·FAQ는 문서 전체를 한 청크로 유지했다.

```text
문서 120개 → 청크 150개
```

### 임베딩 연결 확인

Gemini가 질문과 관련 문서를 실제로 더 가까운 벡터로 표현하는지 먼저 확인했다.

| 비교 | Cosine similarity |
| --- | ---: |
| 질문 ↔ 관련 문서 | `0.7833` |
| 질문 ↔ 무관한 문서 | `0.5683` |

이는 API 연결과 768차원 벡터 생성을 확인한 결과다. 검색 품질은 한 문장이 아니라 고정 질문 20개의 Hit@K로 별도 측정했다.

### 저장 구조

| 테이블 | 저장 내용 |
| --- | --- |
| `documents` | 제목·주제·버전·수정일, 원본 경로, `content_hash` |
| `chunks` | 청크 내용·헤딩 경로, `vector(768)`, 키워드 검색용 `tsv` |

```sql
CREATE TABLE chunks (
  document_id  text NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index  integer NOT NULL,
  heading_path text NOT NULL,
  content      text NOT NULL,
  embedding    vector(768) NOT NULL,
  tsv          tsvector GENERATED ALWAYS AS
               (to_tsvector('simple', content)) STORED,
  PRIMARY KEY (document_id, chunk_index)
);
```

Python은 `content`와 Gemini 임베딩을 저장한다. `tsv`는 PostgreSQL이 자동 생성하며, GIN 인덱스로 키워드 검색에 사용한다.

### 변경된 문서만 다시 색인

```text
Markdown 로딩
→ frontmatter 파싱
→ content_hash 계산
→ DB의 기존 해시와 비교
   ├─ 같음: 메타데이터만 갱신하고 건너뜀
   └─ 없음·다름: 청킹 → 임베딩 → DB 저장
```

`content_hash`에는 정규화한 `title`·`body`, 청킹 버전, 임베딩 입력 버전, 모델명과 차원을 포함한다. 이 중 하나가 바뀌면 문서를 다시 청킹하고 임베딩한다. 청킹과 임베딩 입력에 사용하지 않는 `topic`, `version`, `updated_at`은 해시에서 제외한다.

문서 하나의 모든 벡터가 만들어진 뒤 하나의 트랜잭션으로 기존 청크를 교체한다. 중간에 실패하면 이전 색인을 유지하고, `(document_id, chunk_index)` 기본 키로 중복을 방지한다.

초기 색인 중 Gemini `503 UNAVAILABLE`이 발생했다. 이미 완료된 문서는 `content_hash`로 건너뛰고 실패한 문서부터 이어서 처리했으며, `429`와 `5xx` 오류에는 지수 백오프 재시도를 적용했다.

## 3. 저장 결과 검증

건수만 같아도 오래된 청크가 남아 있을 수 있다. 그래서 현재 Markdown을 다시 파싱·청킹한 예상값과 DB에서 조회한 실제값을 비교했다.

```text
원본 Document → Chunk                ┐
                                      ├─ 내용·해시·건수 비교
PostgreSQL → StoredChunk              ┘
```

검증 항목은 문서 ID·`content_hash`, 청크 키·헤딩·내용, `tsv` 존재 여부와 벡터 차원이다.

```text
source / stored documents       = 120 / 120
expected / stored chunks        = 150 / 150
embedding dimensions (min/max)  = 768 / 768
content hash mismatches         = 0
missing / unexpected chunks     = 0 / 0
chunk content mismatches        = 0
duplicate chunks                = 0
status                          = PASS
```

같은 입력으로 다시 색인했을 때도 120개 문서를 모두 건너뛰고 청크 수를 유지했다.

```text
indexed_documents = 0
skipped_documents = 120
indexed_chunks    = 0
```

## 4. 키워드 검색과 벡터 검색

두 검색 방식 모두 청크에 점수를 매긴 뒤, 같은 문서에서는 최고 점수 청크 하나만 남겨 문서 Top K를 반환한다. 한 문서의 여러 청크가 Top 3를 모두 차지하는 것을 막기 위해서다.

### 키워드 검색

사용자 질문을 OR 조건의 `tsquery`로 바꾸고 제목·헤딩·본문과 비교한다. 제목에는 A, 헤딩에는 B, 본문에는 C 가중치를 주며 `ts_rank_cd`로 상대 점수를 계산한다.

```text
질문: "E-021 오류 대응 방법"
→ 'e-021' | '오류' | '대응' | '방법'
→ 제목(A) + 헤딩(B) + 본문(C) 검색
→ 청크별 ts_rank_cd 점수
```

`ts_rank_cd`는 일치 여부가 아니라 검색 결과의 순서를 정하는 점수다. 같은 단어가 제목에서 일치하면 본문에서만 일치한 경우보다 높은 순위를 받는다.

### 벡터 검색

문서는 색인할 때, 질문은 검색할 때 같은 Gemini 모델로 임베딩한다. PostgreSQL은 텍스트를 임베딩하지 않고 이미 생성된 질문 벡터와 저장된 청크 벡터의 거리를 계산한다.

```sql
1 - (c.embedding <=> query_vector) AS cosine_similarity
```

`<=>`는 cosine distance 연산자다. `1 - distance`로 변환한 similarity가 높을수록 질문과 의미가 가까운 청크다.

## 5. 검색 평가

단일 정답 질문 15개는 Hit@1·Hit@3를, 정답이 두 개인 교차 문서 질문 5개는 필요한 문서가 모두 Top 3에 포함되는지 측정했다.

| 검색 방식 | Hit@1 | Hit@3 |
| --- | ---: | ---: |
| 키워드 | 1/15 (`6.7%`) | 1/20 (`5.0%`) |
| 벡터 | 8/15 (`53.3%`) | 14/20 (`70.0%`) |

| 질문 유형 | 키워드 Hit@3 | 벡터 Hit@3 |
| --- | ---: | ---: |
| 정확한 키워드 | `20%` | `100%` |
| 바꿔 말한 질문 | `0%` | `100%` |
| 최신 정책 | `0%` | `0%` |
| 문서 간 연결 | `0%` | `80%` |

### 확인한 것

- PostgreSQL `simple` 설정은 한국어 조사와 표현 변화를 이해하지 못해 키워드 검색의 재현율이 낮았다.
- 벡터 검색은 바꿔 말한 질문 5개를 모두 Top 3 안에서 찾았다.
- 문서 간 연결 질문도 5개 중 4개에서 필요한 두 문서를 함께 찾았다.
- 두 방식 모두 최신 정책 질문은 하나도 맞히지 못했다.

대표적인 실패는 다음과 같다.

```text
질문: 현재 프로덕션 배포 명령은 무엇인가요?
기대: deploy-guide-v30
벡터 Top 3: faq-q01, deploy-guide-v15, deploy-guide-v21
```

v1~v30의 내용이 비슷하기 때문에 의미 유사도만으로 최신 버전을 판단할 수 없었다. 질문별 실제 Top 3와 성공 여부는 [`evaluation/results-v1.json`](../evaluation/results-v1.json)에 기록했다.

## 6. 출처 기반 답변과 제공 방식

### 출처 기반 답변

벡터 검색 Top 3를 `gemini-3.6-flash`에 전달하고, 제공된 문서만 사용해 답변하면서 근거 번호를 표시하도록 했다.

```text
질문: 배포 가이드 v22에서 변경된 배포 금지 시간은 언제인가요?

답변: 금요일 오후 대신 목요일 오후이며,
      공휴일 전날은 유지됩니다. [1]

[1] 배포 가이드 v22 (deploy-guide-v22, chunk 0)
```

문서에 없는 질문에는 `근거 문서에서 확인할 수 없습니다.`라고 응답하는 것도 확인했다. 다만 최신 문서가 검색되지 않으면 답변 모델이 이를 고칠 수 없다. 검색 품질이 답변 품질의 상한이라는 점을 v1 결과에서 확인했다.

### API와 MCP

검색·답변 로직은 세 개의 FastAPI 엔드포인트로 제공하고, MCP는 이 API를 호출하는 두 개의 도구만 노출한다.

```text
MCP Host
→ search_wiki / ask_wiki
→ FastAPI
→ 검색·답변 파이프라인
→ PostgreSQL + Gemini
```

| 구분 | 이름 | 역할 |
| --- | --- | --- |
| API | `GET /health` | DB 연결 확인 |
| API | `POST /search` | 키워드·벡터 검색 |
| API | `POST /answer` | 답변과 출처 반환 |
| MCP | `search_wiki` | 검색 API 호출 |
| MCP | `ask_wiki` | 답변 API 호출 |

실제 MCP 호출에서 `ask_wiki`가 답변과 `deploy-guide-v22` 출처를 반환하는 것까지 확인했다.

## v1 결론

v1에서는 문서 준비부터 색인·검증·검색·평가·출처 답변까지 하나의 흐름으로 연결했다. 벡터 검색은 표현 변화와 문서 연결에는 효과가 있었지만, 최신성은 해결하지 못했다.

다음 v2에서는 같은 질문 20개를 그대로 사용해 다음 항목을 비교한다.

```text
키워드 + 벡터 hybrid 검색
→ 최신 버전 필터
→ reranker
→ Hit@1·Hit@3 재측정
```
