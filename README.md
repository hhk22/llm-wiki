# LLM Wiki

**사내 문서 검색의 실패를 분석하고, 출처 기반 답변과 Wiki 갱신까지 구현한 개인 프로젝트입니다.**

가상의 배포 가이드·장애 리포트·오류 코드·FAQ 120개를 사용했습니다. RAG 검색을 개선한 뒤, 여러 원본을 종합하는 Wiki와 답변·피드백·평가 기록을 구현했습니다.

**기술:** Python · FastAPI · PostgreSQL/pgvector · Gemini · MCP · SQLite · Docker Compose

[블로그 시리즈](https://velog.io/@khhh9401/series/portfolio-llm-wiki) · [프로젝트 설계](blogs/00-project-design.md) · [실행·개발 안내](docs/usage.md)

## 무엇을 개선했나

| 문제 | 해결과 결과 |
| --- | --- |
| 현재 규칙을 물어도 구버전이 검색됨 | 질문에 맞는 버전 필터로 벡터 검색 **Hit@3 14/20 → 19/20**. [결과](evaluation/results-v2-filters.json) |
| 정답 문서를 찾아도 필요한 근거가 빠짐 | 문서당 최대 2개 청크를 전달해 최신 정책 5문항의 **근거 확보 4/5 → 5/5**. [결과](evaluation/results-v2-evidence.json) |
| 검색을 결합해도 성능이 나빠짐 | 동일 가중치 Hybrid **11/20**, 벡터 **14/20**을 확인하고 기본 검색은 벡터로 유지. [비교](evaluation/results-v2-hybrid.json) |

가상 문서와 고정 20문항에서 측정했습니다. Hit@3는 필요한 문서가 상위 3개에 포함되는 지표이며, 최종 답변 정확도와는 다릅니다.

## 어떻게 동작하나

```text
질문 → MCP 또는 HTTP API
    ├─ RAG: 원본 검색 → 근거 기반 답변
    └─ Wiki: 미리 생성한 Wiki 탐색 → 출처 기반 답변
                           ↓
             답변·출처·실행 기록 저장 → answer_id 반환
```

- **Wiki 갱신:** 원본 변경 후 갱신 명령을 실행하면 영향받는 내용을 재생성하고 검증 후 교체합니다.
- **답변 검토:** 별도 호출로 피드백을 저장하고, 사람이 원문을 검토한 평가를 추가합니다. 평가가 Wiki를 자동 수정하지는 않습니다.

생성된 [Wiki 목차](wiki/index.md)와 [실제 질문·후속 질문 실행 기록](evaluation/wiki-conversation-review.md)을 확인할 수 있습니다.

<a id="quick-start"></a>

## 빠른 시작

Git, Docker Compose, Gemini API 키가 필요합니다. 답변 생성 시 API 비용이 발생할 수 있습니다.

```bash
git clone https://github.com/hhk22/llm-wiki.git
cd llm-wiki
[ -f .env ] || cp .env.example .env
# .env의 GEMINI_API_KEY 설정 후 실행
docker compose up -d --build
```

[Swagger UI](http://127.0.0.1:8000/docs)의 `POST /answer`에 아래 JSON을 입력합니다. 저장소에 포함된 Wiki를 사용하므로 별도 벡터 색인이 필요 없습니다.

```json
{"query":"현재 배포 금지 시간과 목요일 오후가 금지된 이유는?","method":"wiki","top_k":3}
```

응답으로 `answer`, `sources`, `answer_id`를 받습니다. RAG 색인·MCP 연결·테스트 방법은 [실행 안내](docs/usage.md)에 있습니다.

<a id="devlogs"></a>

## 구현 기록

| 단계 | 상세 글 |
| --- | --- |
| v1 · 검색 기준선과 실패 분석 | [RAG 기준선](blogs/01-rag-baseline.md) |
| v2 · 검색·근거 개선과 전후 비교 | [검색 품질 개선](blogs/02-search-quality.md) |
| v3 · Wiki 생성과 후속 질문 | [LLM Wiki 구축](blogs/03-llm-wiki.md) |
| v4 · 원본 변경에 따른 부분 갱신 | [Wiki 갱신](blogs/04-wiki-updates.md) |
| v5 · 운영 개선 **설계** | [한계와 도입 조건](blogs/05-performance-stability.md) |
| v6 · 답변·피드백·사람의 평가 저장 | [기록과 평가](blogs/06-evaluation-feedback.md) |

RAG 대비 Wiki의 품질·비용 우위는 아직 측정하지 않았습니다. v5의 Worker·캐시 등은 설계 단계입니다.
