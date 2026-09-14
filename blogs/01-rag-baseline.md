# LLM Wiki v1 — RAG 기준선 ① 문서 준비와 색인

> v1의 목표는 RAG를 잘 만드는 것이 아니다. **RAG가 어디까지 되고 어디서 안 되는지 측정**해서, 이후 LLM Wiki로 전환하는 근거를 만드는 것이다.

이 글은 v1의 앞부분, 실험에 쓸 문서를 준비하고 DB에 넣는 과정을 다룬다. 검색·답변·평가는 다음 글에서 이어간다.

## 어떤 문서로 실험할까

가상 회사의 개발 문서 120개를 만든다. 주제는 4개로 제한하고, 각 주제는 같은 템플릿의 번호 나열이다. 문서는 `sources/` 아래 주제별 폴더에 Markdown으로 둔다.

| 주제 | 문서 | 형식 |
| --- | --- | --- |
| 배포 가이드 | v1 ~ v30 | 버전마다 한 가지만 바뀐다 |
| 장애 리포트 | #01 ~ #30 | 날짜 · 서비스 · 증상 · 원인 · 조치 |
| 에러 코드 | E-001 ~ E-030 | 의미 · 원인 · 대응 |
| 온보딩 FAQ | Q01 ~ Q30 | 질문 하나 · 답 하나 |

문서 하나는 5~10줄이다.

```markdown
# 배포 가이드 v22
- 변경: 배포 금지 시간을 금요일 오후에서 목요일 오후로 변경
- 이유: 장애 리포트 #18 이후 결정
- 배포 명령: deploy run --env prod
- 롤백 명령: deploy rollback --to <version>
```

## 문서는 어떻게 DB에 들어가나

### 일반적인 구성

실제 서비스에서는 문서가 계속 바뀐다. 변경된 문서만 비동기로 처리하고, 실패한 작업은 상태를 남긴 뒤 재시도한다.

![문서 변경부터 색인 완료까지의 비동기 파이프라인](https://raw.githubusercontent.com/hhk22/llm-wiki/docs/llm-wiki-devlog/images/indexing-pipeline-flow-v1.gif)

각 구성 요소의 역할은 다음과 같다.

| GIF 단계 | 구성 요소 | 하는 일 |
| --- | --- | --- |
| 2. 변경 감지 | 스케줄러 또는 웹훅 | 원본 저장소를 확인하고, 수정 시각·해시로 바뀐 문서만 골라낸다. |
| 3–4. 작업 큐·Worker | 큐 + Worker | 색인 요청을 분리해 처리하고, 파싱 → 청킹 → 임베딩 → 저장을 실행한다. |
| 5. 작업 상태 | 작업 상태 기록 | 성공·실패·재시도 상태를 남기고, 실패한 작업만 큐로 되돌린다. |

#### 새 commit만 어떻게 반영할까?

```text
A commit (9.14) → 인덱싱 작업 1 → B commit (9.15) → 인덱싱 작업 2
```

인덱싱 작업 2에서는 작업 1이 반영한 `A`를 제외하고, `A` 이후 `B`까지 바뀐 파일만 찾아 처리한다. 보통 마지막 반영 commit은 DB의 별도 상태 테이블에 `indexed_commit=A`처럼 저장한다.

```text
git diff --name-status A B
  → 추가·수정·삭제된 파일만 작업 큐에 등록
```

작업이 모두 성공하면 `indexed_commit`을 `B`로 바꾼다. 실패하면 `A`를 유지해 다음 실행에서 다시 확인한다.

### 이 프로젝트의 선택: 일괄 색인

v1에서는 위 구성을 만들지 않는다. **명령 한 번으로 120개를 한 번에 색인한다.**

```text
sources/**/*.md → ingest → frontmatter 파싱 → 헤딩 기준 청킹 → 임베딩 API → PostgreSQL
```

v1은 사내 문서 검색 시스템의 초기 버전이다. 변경 추적·재시도 등은 이후 버전에서 단계적으로 개선한다. 문서 120개 규모에서는 명령 한 번으로 일괄 색인하면 충분하다.

참고) 본문 해시를 저장해 **바뀌지 않은 문서는 다시 임베딩하지 않는다.**

## 저장 구조

테이블은 두 개다.

| 테이블 | 저장하는 것 |
| --- | --- |
| `documents` | 문서 메타데이터(제목·주제·버전·수정일), 원본 파일 경로, 본문 해시 |
| `chunks` | 청크 원문, 소속 문서와 섹션 경로, 임베딩 벡터, 키워드 색인 |

```sql
CREATE TABLE documents (
  id           text PRIMARY KEY,
  source_path  text NOT NULL UNIQUE,
  title        text NOT NULL,
  topic        text NOT NULL,
  version      text,
  updated_at   date,
  content_hash text NOT NULL
);

CREATE TABLE chunks (
  id           bigserial PRIMARY KEY,
  document_id  text REFERENCES documents(id),
  heading_path text,                 -- "배포 가이드 v22 > 롤백"
  content      text,
  embedding    vector(1024),         -- pgvector
  tsv          tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
);
```

- **임베딩은 외부 API로 만들고, pgvector는 저장과 거리 계산만 한다.** pgvector가 벡터를 만들어 주지는 않는다.
- **`tsv` 컬럼은 키워드 검색 기준선용이다.** 벡터 검색과 비교하기 위해 같은 테이블에 둔다.

## 다음 글

색인한 청크로 질문에 답하는 부분을 만든다. 벡터 검색과 키워드 검색을 같은 평가 질문으로 돌려 유형별로 어디서 맞고 어디서 틀리는지 기록한다.
