# LLM Wiki — 프로젝트 설계

> 사내 문서를 쉽게 찾고, 최신 문서를 근거로 질문을 이어갈 수 있는 Wiki를 만든다.

## 시작 배경

업무에 AI Agent를 활용하다 보면 이런 생각이 들 수 있다.

- **필요한 사내 문서도 AI Agent에게 물어서 찾을 수 없을까?**
- **찾은 문서를 대화의 맥락으로 삼아, 추가 질문까지 이어갈 수 없을까?**

문서를 찾는 과정과 그 내용을 이해하고 업무에 적용하는 과정이 하나의 대화로 이어지는 경험을 목표로 한다.

이런 흐름을 지원하는 **LLM Wiki**를 만들어보려 한다. 사내 구현·배포 경험을 바탕으로, 가상 문서를 활용한 공개 프로젝트의 설계와 구현 과정을 기록한다.

## 누가, 어떻게 사용할까?

대상은 **업무 중 사내 문서를 찾아보는 모든 구성원**이다. 출장비 정산을 예로, 다음과 같은 사용 흐름을 구현하려 한다.

*아래 문서·메뉴·답변은 공개 데모를 위한 가상 예시다.*

![LLM Wiki 질문과 후속 대화 흐름](https://raw.githubusercontent.com/hhk22/llm-wiki/master/images/llm-wiki-flow.gif)

## 버전별 전개

v1~v4에서 검색 개선, Wiki 구축·질의응답과 원본 변경에 따른 갱신을 구현했다. v5는 현재 한계와 개선 설계를 글로 정리하고, v6에서는 평가·피드백 기록과 같은 조건의 비교 실행 도구를 구현했다. 실제 모델의 비교 측정과 의미 평가는 별도로 수행해야 한다.

| 버전 | 내용과 범위 |
| --- | --- |
| **[v1 · RAG 기준선](https://github.com/hhk22/llm-wiki/blob/docs/llm-wiki-devlog/blogs/01-rag-baseline.md) (완료)** | 가상 문서 120개·청크 150개를 색인하고 출처 답변·API·MCP를 구현했다. 고정 질문 20개에서 벡터 Hit@3 14/20, 최신 정책 0/5를 기록했다. |
| **[v2 · 검색 품질 개선](https://github.com/hhk22/llm-wiki/blob/v2-search-quality/blogs/02-search-quality.md) (완료)** | 식별자 보정, hybrid, 최신 버전 필터와 원인 장애 참조를 구현하고 검색 품질을 비교했다. |
| **[v3 · LLM Wiki 전환](https://github.com/hhk22/llm-wiki/blob/master/blogs/03-llm-wiki.md) (구현 완료)** | Wiki 본문·목차·관련 링크·출처 생성, 시점·이력·상충 표시, Wiki 기반 질의응답과 후속 질문 처리를 구현했다. |
| **[v4 · 지식 갱신](https://github.com/hhk22/llm-wiki/blob/master/blogs/04-wiki-updates.md) (구현·자동 테스트 검증)** | 원본 추가·수정·삭제를 감지하고 영향받는 Wiki·이력·상충을 갱신한다. 검증 후 교체하고 이전 결과와 갱신 기록을 보관한다. |
| **[v5 · 성능과 안정성](https://github.com/hhk22/llm-wiki/blob/master/blogs/05-performance-stability.md) (설계 검토)** | 갱신 중 대화의 버전 유지, Worker와 상태 조회, 문서 증가에 따른 목차 계층화·Wiki 검색 결합을 설계한다. |
| **[v6 · 평가와 피드백](https://github.com/hhk22/llm-wiki/blob/master/blogs/06-evaluation-feedback.md) (구현·자동 테스트 검증)** | 답변·출처·응답 상태·시간·토큰과 검토 결과를 기록한다. 기존 검색과 Wiki 답변, 전체 재구축과 부분 갱신을 같은 조건으로 비교하고 사용자 피드백을 재검증할 사례로 남긴다. |

자세한 코드는 여기서 확인할 수 있습니다. [https://github.com/hhk22/llm-wiki](https://github.com/hhk22/llm-wiki)
