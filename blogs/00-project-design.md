# LLM Wiki — 프로젝트 개요와 구현 결과

> 사내 문서의 검색 실패를 분석하고, 출처 기반 답변·Wiki 구축과 갱신·피드백 기록까지 연결한 프로젝트다.

가상 사내 문서 120개를 대상으로 PostgreSQL·pgvector 기반 RAG와 FastAPI·MCP를 구현했다. 검색 실패를 분석해 최신 버전 선택과 근거 누락을 개선하고, 원본을 종합한 Wiki의 구축·질의·부분 갱신 기능을 추가했다. 답변 기록에 사용자 피드백과 사람의 평가를 연결해 나중에 검토할 수 있도록 구성했다.

**실제 측정한 검색 개선, 구현 후 기능을 확인한 부분, 아직 설계만 한 부분을 구분해 기록한다.** 실제 사내 문서를 사용한 운영 성과나 Wiki의 RAG 대비 품질·비용 우위를 입증한 프로젝트는 아니다.

## 먼저 볼 결과와 판단

| 문제와 판단 | 확인한 결과 | 근거 |
| --- | --- | --- |
| 유사도만으로 최신 문서를 선택하지 못해 버전 필터를 추가했다. | 고정 20문항의 벡터 문서 Hit@3가 14/20 → 19/20으로 개선됐다. | [필터 비교 결과](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/evaluation/results-v2-filters.json) |
| Hybrid를 구현했지만 기본 검색으로 채택하지 않았다. | 동일 가중치 RRF는 11/20으로 벡터 14/20보다 낮았다. | [Hybrid 비교 결과](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/evaluation/results-v2-hybrid.json) |
| 정답 문서의 선택과 답변에 필요한 근거 확보를 따로 확인했다. | 문서당 청크 2개 전달로 최신 정책 근거 확보가 4/5 → 5/5로 개선됐다. 원인 참조 보강의 후보 재평가에서는 문서 Hit@3가 19/20 → 20/20이었다. | [청크 비교](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/evaluation/results-v2-evidence.json) · [후보 재평가](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/evaluation/results-v2-followups.json) |

문서 Hit@3는 필요한 원본 문서가 상위 3개에 있는지를 보는 지표다. **20/20은 저장된 후보와 DB를 이용한 회귀 평가 결과이며, 답변 정확도 100%나 새로운 질문에서의 성능을 뜻하지 않는다.**

Wiki 본문 120개·목차 5개를 구축하고 실제 모델로 질의·후속 질문 등 네 사례를 각 1회 확인했다. 갱신과 기록 저장은 자동 테스트로 동작을 검증했다. 같은 조건의 RAG·Wiki 답변 비교와 전체·부분 갱신의 실제 모델 반복 측정은 아직 수행하지 않았다.

## 시작 배경

업무에 AI Agent를 활용하다 보면 이런 생각이 들 수 있다.

- **필요한 사내 문서도 AI Agent에게 물어서 찾을 수 없을까?**
- **찾은 문서를 대화의 맥락으로 삼아, 추가 질문까지 이어갈 수 없을까?**

문서를 찾는 과정과 그 내용을 이해하고 업무에 적용하는 과정이 하나의 대화로 이어지는 경험을 목표로 한다.

이 흐름을 지원하는 **LLM Wiki**를 구현했다. 사내 구현·배포 경험에서 문제를 골랐으며, 공개 저장소에는 배포 가이드·장애 리포트·오류 코드·온보딩 FAQ로 구성한 가상 문서를 사용했다.

## 누가, 어떻게 사용할까?

아래는 출장비 정산 질문으로 표현한 **사용 흐름의 가상 데모**다. 문서·메뉴·답변은 설명용이며, 실제 구현 확인에 사용한 배포 정책 예시는 이어서 제시한다.

![LLM Wiki 질문과 후속 대화 흐름](https://raw.githubusercontent.com/hhk22/llm-wiki/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/images/llm-wiki-flow.gif)

대상은 **업무 중 사내 문서를 찾아보는 구성원**이다. 아래는 저장소의 배포 정책을 사용한 대화 예시다. 문장은 설명을 위해 줄였으며, 실제 기능 확인 기록은 [질의·대화 검증 기록](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/evaluation/wiki-conversation-review.md)에 있다.

```text
사용자: “현재 배포 금지 시간과 목요일 오후가 금지된 이유를 알려줘.”
    ↓
MCP ask_wiki → API → 근거 탐색 → 답변 생성
    ↓
답변: “목요일 오후·공휴일 전날·연말 동결 기간에는 배포하지 않습니다.
       목요일 오후에는 정산 배치와 배포가 충돌한 장애가 있었습니다.” + 출처
    ↓
사용자: “그럼 목요일 오후로 바뀌기 전에는?”
    ↓
호출자가 앞선 대화를 history로 전달 → 질문 대상 해석 → 근거를 다시 탐색
    ↓
답변: “금요일 오후·공휴일 전날이었습니다.” + 출처
```

## 최종 시스템의 흐름

```text
원본 Markdown
    ├─ 색인 명령 → 청킹·임베딩 → PostgreSQL 검색 데이터
    └─ Wiki 구축·갱신 명령 → 종합된 Markdown·목차·출처

사용자 질문 → MCP 도구 호출 → API
    ├─ RAG: PostgreSQL 검색 → 근거 청크로 답변
    └─ Wiki: 목차·링크 탐색 → Wiki 근거로 답변

두 경로 모두: API 답변·실행 기록을 SQLite에 저장 → 호출 클라이언트에 반환

사용자 피드백 → 별도 MCP/API 호출 → feedback 저장
    ↓
사람의 원문 검토 → 별도 평가 API/스크립트 실행 → evaluations 저장
```

RAG와 Wiki는 선택 가능한 두 질의 경로다. Wiki는 원본에서 연결된 설명을 미리 정리해 재사용하는 구조를 실험하기 위해 추가했다. 기존 RAG도 현재 규칙과 변경 이유를 함께 답한 사례가 있어, RAG의 종합 답변 실패를 전제로 전환하지 않았다. [전환 전 기준선 기록](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/evaluation/v2-v3-baseline-review.md)

원본 수정만으로 갱신 명령이 실행되지는 않는다. 피드백·평가 저장도 별도 호출이며, 저장된 평가를 Wiki·검색 정책에 자동 적용하는 단계는 구현하지 않았다.

## 버전별 전개

v1~v4에서 검색 개선, Wiki 구축·질의응답과 원본 변경에 따른 갱신을 구현했다. v5는 현재 한계와 개선 설계를 글로 정리하고, v6에서는 평가·피드백 기록과 같은 조건의 비교 실행 도구를 구현했다. 실제 모델의 비교 측정과 의미 평가는 별도로 수행해야 한다.

| 버전 | 내용과 범위 |
| --- | --- |
| **[v1 · RAG 기준선](https://github.com/hhk22/llm-wiki/blob/v6-evaluation-feedback/blogs/01-rag-baseline.md) (완료)** | 가상 문서 120개·청크 150개를 색인하고 출처 답변·API·MCP를 구현했다. 고정 질문 20개에서 벡터 Hit@3 14/20, 최신 정책 0/5를 기록했다. |
| **[v2 · 검색 품질 개선](https://github.com/hhk22/llm-wiki/blob/v6-evaluation-feedback/blogs/02-search-quality.md) (완료)** | 식별자 보정, hybrid, 최신 버전 필터와 원인 장애 참조를 구현하고 검색 품질을 비교했다. |
| **[v3 · Wiki 구축과 질의](https://github.com/hhk22/llm-wiki/blob/v6-evaluation-feedback/blogs/03-llm-wiki.md) (구현·기능 확인)** | Wiki 본문·목차·관련 링크·출처 생성, 시점·이력·상충 표시와 후속 질문을 구현했다. 실제 모델의 단회 기능 확인이며 RAG 대비 우위는 미측정이다. |
| **[v4 · 지식 갱신](https://github.com/hhk22/llm-wiki/blob/v6-evaluation-feedback/blogs/04-wiki-updates.md) (구현·자동 테스트 검증)** | 원본 추가·수정·삭제를 감지하고 영향받는 Wiki·이력·상충을 갱신한다. 검증 후 교체하고 이전 결과와 갱신 기록을 보관한다. |
| **[v5 · 성능과 안정성](https://github.com/hhk22/llm-wiki/blob/v6-evaluation-feedback/blogs/05-performance-stability.md) (설계 검토)** | 갱신 중 대화의 버전 유지, Worker와 상태 조회, 문서 증가에 따른 목차 계층화·Wiki 검색 결합을 설계한다. |
| **[v6 · 답변 기록과 피드백·평가 저장](https://github.com/hhk22/llm-wiki/blob/v6-evaluation-feedback/blogs/06-evaluation-feedback.md) (구현·자동 테스트 검증)** | API 답변·실행 기록에 사용자 피드백과 사람의 평가를 연결한다. 비교 실행 도구는 구현했으며 실제 모델의 반복 비교는 미실행이다. |

초기 계획에 있던 reranker, Worker·작업 큐, Redis 캐시는 구현하지 않았다. v5는 필요한 운영 조건과 개선안을 정리한 설계 검토다.

## 코드를 다시 읽는 순서

1. **검색 개선의 판단과 측정:** v1 → v2. 질문 세트·검색 결과·선택한 근거를 함께 확인한다.
2. **Wiki를 만드는 과정과 갱신:** v3 → v4. 생성 입력·출처·원본 의존성과 교체 조건을 확인한다.
3. **답변 이후의 검토:** v6. 질문 → API 답변 기록 → 피드백 → 사람이 작성한 평가 저장 순서로 확인한다.
4. **구현 이후의 운영 과제:** v5. 현재 코드와 구분해서 읽는다.

각 글의 코드·실험 근거 링크는 해당 시점의 커밋으로 고정했다. 시리즈 글 사이의 링크는 [v6 브랜치의 원고](https://github.com/hhk22/llm-wiki/tree/v6-evaluation-feedback/blogs)로 연결한다. 실행 방법은 [v6 기준 README](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/README.md)에 있다.
