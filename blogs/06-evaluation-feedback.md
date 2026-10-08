# LLM Wiki v6 — 답변 기록과 사용자 피드백·평가 저장

> **잘못된 답변을 나중에 검토할 수 있도록, 당시의 근거와 사용자의 지적, 사람이 확인한 결과를 남긴다.**

v4까지 Wiki 구축·질의응답·원본 변경에 따른 갱신을 구현했고, v5에서는 성능과 안정성의 개선 방향을 정리했다. v6에서는 답변마다 기록을 남기고, 별도 호출로 사용자 피드백과 사람의 평가를 저장하는 기능을 추가했다.

**이 글의 중심 범위는 `evaluations` 테이블에 사람이 작성한 평가를 저장하는 단계까지다.** 평가를 바탕으로 Wiki를 갱신할지, 탐색·답변 정책을 바꿀지 결정하고 적용하는 연결은 이후 작업이다.

```text
사용자가 질문
    ↓
서버: answer_id 발급 → 답변 생성 → answers에 기록 저장 → 답변 반환
    ↓
개발자·클라이언트·에이전트: 피드백 API 또는 MCP 도구 호출
    ↓
서버: feedback에 사용자 피드백 저장
    ↓
검토자: get_answer_record 또는 조회 API로 당시 답변·피드백 조회
    ↓
사람: 원문과 대조하고 검토 결과 작성
    ↓
    ├─ 경로 1: 평가 JSON 파일 → review_answers.py → evaluations 저장
    └─ 경로 2: JSON 본문을 담은 HTTP 요청 → 평가 API → evaluations 저장
```

피드백 저장과 평가 저장은 각각 별도 호출이다. 피드백이 들어온 뒤 원인을 분석하고 평가를 작성하는 과정은 사람이 수행한다.

## 1. 질문에 답할 때 당시의 근거도 함께 저장한다

다음은 동작을 설명하기 위해 만든 **가상의 오답 사례**다. 최신 승인자 규칙은 저장소의 [배포 가이드 v30](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/sources/deploy-guide/v30.md)에 맞췄지만, 실제 실행에서 관측한 실패 결과는 아니다.

- 현재 원문에는 배포 승인자가 **팀 리드와 서비스 오너**라고 적혀 있다.
- 과거 원문에는 **팀 리드**만 적혀 있다.
- 시스템이 과거 문서를 참고해 잘못 답했다고 가정한다.

> 사용자: “지금 배포하려면 누구 승인이 필요해?”
>
> 시스템: “팀 리드의 승인이 필요합니다.”

`POST /answer`는 유효한 요청에 `answer_id`를 발급한다. 답변을 생성한 뒤 질문·답변·출처·실행 기록을 `answers` 테이블에 저장하고, 그다음 호출한 클라이언트에 답변과 ID를 반환한다.

**여기서 저장하는 답변은 API가 생성한 응답이다.** Codex가 MCP 결과를 받아 사용자에게 다시 정리해 말한다면, 그 최종 대화 문장까지 이 DB에 저장되는 것은 아니다. ID 역시 호출 결과에 포함되며, 사용자 화면에 항상 표시하도록 강제하지는 않는다.

설명을 위해 이 답변의 ID를 `A001`이라고 부르겠다. 실제 API에서는 발급받은 UUID를 사용한다.

```text
answers에 저장된 A001의 내용 — 설명을 위해 요약한 형태

질문: 지금 배포하려면 누구 승인이 필요해?
답변: 팀 리드의 승인이 필요합니다.
참고한 근거: 과거 배포 가이드의 “승인자: 팀 리드”
실행 기록: 검색 결과 또는 읽은 Wiki 경로, 모델 호출, 시간·토큰 등
```

아직 사용자가 오류를 지적하지 않았어도 기록을 저장한다. 나중에 같은 질문을 다시 실행하면 다른 문서를 찾거나 다른 답을 만들 수 있으므로, **당시 API가 반환한 답변과 그 근거**를 보존해야 하기 때문이다.

기본 저장소는 `.local/evaluation.sqlite3`이다. 기록에는 요청으로 받은 대화 이력, 응답 상태, 코드 해시, Wiki 구축 지문 또는 검색 문서의 색인 해시도 포함된다. 후속 질문의 `history`는 호출자가 요청에 넣어야 하며, 서버가 이 DB에서 이전 대화를 자동으로 가져오지는 않는다.

구현: [답변 API](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/api.py)의 `answer()`, [기록 저장소](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/answer_records.py)의 `AnswerStore.save()`.

## 2. 사용자의 지적은 별도 호출로 feedback에 저장한다

사용자가 답변을 보고 문제를 발견한다.

> 사용자: “틀렸어. 서비스 오너 승인도 필요한데 빠졌잖아.”

이 말 자체가 자동으로 DB에 저장되는 것은 아니다. 개발자나 클라이언트가 피드백 API를 호출하거나, 대화 중인 에이전트가 `submit_answer_feedback` MCP 도구를 호출해야 한다. 별도 피드백 화면은 구현 범위에 포함하지 않았다.

예를 들어 개발자가 A001의 실제 ID로 `POST /answers/{answer_id}/feedback`을 호출한다.

```json
{
  "rating": "unhelpful",
  "category": "answer",
  "comment": "서비스 오너 승인도 필요한데 답변에서 빠졌어요."
}
```

서버는 이 내용을 `feedback` 테이블에 저장하고 A001에 연결한다. 이때 `category`도 호출자가 입력한 분류다. 서버가 원인을 분석해 붙인 결과가 아니다. `unhelpful`에는 이유를 담은 코멘트가 필요하다.

```text
A001의 답변 기록
    └─ 사용자 피드백: “서비스 오너 승인이 빠졌어요.”
```

**여기까지는 사용자의 지적을 접수한 상태다.** 원문을 확인해 답변이 틀렸다고 판정하거나, 원인을 분석하거나, Wiki를 수정하는 작업은 실행하지 않는다.

구현: [기록 저장소](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/answer_records.py)의 `AnswerStore.add_feedback()`, [MCP 도구](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/mcp_server.py)의 `submit_answer_feedback()`.

## 3. 사람이 접수된 피드백을 검토한다

검토의 출발점은 사용자가 남긴 피드백이다. 이 사례에서 개발자나 검토자가 확인할 항목은 **“서비스 오너 승인이 빠졌어요”라는 지적이 맞는지, 맞다면 왜 누락됐는지**다.

피드백 내용만으로는 이를 판단할 수 없으므로, 연결된 답변 ID인 A001로 당시 기록을 조회한다. `GET /answers/{answer_id}` 또는 `get_answer_record` MCP 도구를 호출하면 당시 요청·답변·실행 기록과 연결된 피드백·평가를 함께 확인할 수 있다. 현재 API·MCP의 조회 기능은 답변 ID 단위이며, 미검토 피드백 목록을 모아 보여주는 기능은 별도로 구현하지 않았다.

이 사례에서 검토자는 다음 내용을 대조한다.

| 확인 대상 | 사례에서 확인한 내용 |
| --- | --- |
| 사용자 질문 | 현재 배포 승인자를 물었다. |
| 실제 답변 | 팀 리드만 안내했다. |
| 당시 사용한 근거 | 과거 가이드의 “승인자: 팀 리드”를 사용했다. |
| 현재 원문 | 팀 리드와 서비스 오너의 승인이 필요하다. |

이를 보고 검토자는 “현재 규칙을 묻는 질문에 과거 규칙을 근거로 답했다”고 판단한다. 만약 당시 읽은 문서에 두 승인자가 모두 적혀 있었다면, 답변 생성 중 누락했는지 살펴봐야 한다.

즉, **검토 대상은 접수된 피드백이고, 답변 기록과 원문은 그 지적을 확인하고 원인을 조사하는 자료**다. 현재 구현에는 피드백을 입력받아 원인을 자동 분석하는 함수가 없다. 평가 API 자체는 피드백이 없어도 사용할 수 있지만, 여기서는 접수된 피드백을 계기로 검토하는 흐름을 설명한다.

## 4. 검토 결과를 별도 호출로 evaluations에 저장한다

검토자가 작성한 결과를 저장하는 경로는 두 가지다. **평가를 저장하는 전용 MCP 도구는 없다.** A001에 대한 공통 입력 형태는 다음과 같다.

```json
{
  "reviewer": "reviewer-1",
  "verdict": "incorrect",
  "evidence": "mixed",
  "reason": "현재 승인자를 물었지만 과거 가이드를 근거로 답했다. 팀 리드는 맞으나 현재 필요한 서비스 오너 승인이 누락됐다.",
  "expected": "팀 리드와 서비스 오너의 승인이 필요하다고 현재 원문을 근거로 답한다.",
  "expected_status": "answered"
}
```

`verdict`는 정오 판정, `evidence`는 근거 평가, `reason`은 검토 이유, `expected`는 기대 답이다. 모두 검토자가 작성한 값이다. 코드는 허용된 값과 필수 항목 등 **입력 형식을 검증하며, 평가 내용이 사실인지 판단하지 않는다.**

### 경로 1 — 평가 JSON 파일을 스크립트에 전달한다

```text
검토자가 위 내용을 .local/review.json으로 작성
    ↓
개발자가 review_answers.py 실행
    ↓
EvaluationInput으로 JSON 검증 → AnswerStore.evaluate() → evaluations 저장
```

```bash
# ANSWER_ID는 실제로 저장된 답변의 UUID로 바꾼다.
uv run python scripts/review_answers.py \
  --records .local/evaluation.sqlite3 \
  --answer-id ANSWER_ID \
  --evaluation .local/review.json
```

`--records`는 평가 대상 답변이 들어 있는 SQLite 파일, `--evaluation`은 한 건의 평가 JSON 파일이다. 평가 JSON에 `answer_id`를 넣는 대신 `--answer-id`로 대상을 지정한다.

`review.json`은 평가를 전달하기 위한 입력 파일이다. 스크립트가 만들거나 내용을 누적하거나 실행 후 삭제하지 않는다. 다음 검토에서 덮어쓸지 별도 파일로 보관할지는 작성자가 정한다. **누적되는 평가 이력은 DB에 저장된다.** 같은 파일을 다시 실행해도 새 평가 행이 추가되므로, 자동 중복 제거를 기대하면 안 된다.

### 경로 2 — 평가 API에 JSON 본문을 보낸다

```text
개발자·클라이언트가 POST /answers/{실제 answer_id}/evaluation 호출
    ↓
HTTP 요청 본문에 위 평가 JSON 전달
    ↓
EvaluationInput으로 검증 → AnswerStore.evaluate() → evaluations 저장
```

이 경로는 JSON 파일 업로드가 아니다. 같은 구조의 JSON 데이터를 HTTP 본문으로 보내므로 `review.json` 파일을 만들 필요가 없다. 대상 ID는 URL에 넣는다. 호출 예시는 [README의 v6 안내](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/README.md#v6--답변-기록피드백비교-평가)에 정리했다.

저장 후 A001을 조회하면 다음 세 종류의 기록을 함께 볼 수 있다.

```text
A001
  answers:     “팀 리드의 승인이 필요합니다.” + 당시 요청·근거·실행 기록
  feedback:    “서비스 오너 승인이 빠졌어요.”
  evaluations: 사람이 확인한 판정·이유·기대 답
```

기존 답변과 피드백은 그대로 보존한다. 평가를 다시 제출하면 이전 평가를 덮어쓰지 않고 추가하며, 비교·사례 내보내기에는 최신 평가를 사용한다.

구현: [기록 저장소](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/answer_records.py)의 `EvaluationInput`과 `AnswerStore.evaluate()`. **`evaluate()`는 입력받은 평가를 저장하는 함수이며, 스스로 정답이나 원인을 판단하지 않는다.**

### 실행 순서대로 볼 코드

| 단계 | 코드 흐름 |
| --- | --- |
| 답변 저장 | [API answer()](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/api.py#L307) → `execute_answer()` → [AnswerStore.save()](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/answer_records.py#L84) |
| 피드백 저장 | [MCP submit_answer_feedback()](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/mcp_server.py#L53) → HTTP 클라이언트 → [API feedback()](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/api.py#L217) → [add_feedback()](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/answer_records.py#L107) |
| 검토 자료 조회 | [MCP get_answer_record()](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/mcp_server.py#L62) → [API get_answer()](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/api.py#L210) → [AnswerStore.get()](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/answer_records.py#L89) |
| 평가 저장 · 경로 1 | [review_answers.py](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/scripts/review_answers.py#L29) → `EvaluationInput` 검증 → [AnswerStore.evaluate()](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/answer_records.py#L118) |
| 평가 저장 · 경로 2 | [API evaluate()](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/api.py#L223)의 입력 검증 → [AnswerStore.evaluate()](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/src/llm_wiki/answer_records.py#L118) |

## 5. 저장된 평가를 Wiki 갱신에 연결하는 것은 이후 작업이다

A001에 위 평가를 저장해도 Wiki 내용과 답변 정책은 바뀌지 않는다. 현재 Wiki 갱신기는 `sources/`의 원본 변경을 감지하며, `evaluations` 테이블을 읽어 갱신하지 않는다.

예를 들어 이 사례를 실제로 개선하려면 사람이 원인에 따라 다음 작업을 선택해야 한다.

| 발견한 문제의 예 | 이후에 선택할 작업 |
| --- | --- |
| 원본에 서비스 오너 승인 규칙 자체가 빠져 있다. | 확인된 정책에 맞게 원본을 보완하고 Wiki 갱신을 실행한다. |
| 원본은 맞지만 생성된 Wiki에서 서비스 오너을 누락했다. | Wiki 생성 로직·프롬프트 등을 검토하고 재생성한다. |
| Wiki는 맞지만 과거 페이지를 선택하거나 답변에서 조건을 누락했다. | 탐색·답변 로직이나 정책을 수정한다. |

**평가에서 수정 대상을 결정하고, 변경을 적용하는 연결은 아직 구현하지 않았다.** v6에서는 그 결정을 내릴 때 참고할 답변·피드백·검토 결과를 보존한다.

재검증을 위한 보조 도구는 마련돼 있다. 예를 들어 A001처럼 사람의 평가가 있는 기록은 “배포 승인자가 누구인가?”라는 질문과 기대 답을 평가 사례로 내보낼 수 있다. 개발자가 수정 작업을 마친 뒤 비교 도구를 별도로 실행하면 같은 질문의 새 답변을 기록할 수 있다.

| 보조 도구 | 현재 가능한 작업 |
| --- | --- |
| [review_answers.py](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/scripts/review_answers.py) | 사람이 작성한 평가 저장. 선택 옵션으로 원래 질문·대화 이력과 최신 평가의 기대 답을 `case.json`으로 내보내거나, 비교 보고서에 저장된 평가를 붙임 |
| [compare_answers.py](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/scripts/compare_answers.py) | 사례 파일의 질문으로 방법별 새 답변 생성. 비교 보고서 JSON과 답변 기록 SQLite를 별도로 저장 |
| [compare_wiki_updates.py](https://github.com/hhk22/llm-wiki/blob/4c7caa1bdc5dc67adc118fd0d2d01484380592b1/scripts/compare_wiki_updates.py) | 변경 전·후 원본 폴더로 전체 재구축과 부분 갱신을 각각 실행하고 시간·토큰·재생성 범위를 보고서에 기록 |

이 도구들도 별도 실행이 필요하다. 기대 답은 평가에 사용하며, Wiki 생성이나 답변 모델의 입력에 넣지 않는다. 새 답변의 의미적 정확성은 사람이 검토한다.

`case.json` 내보내기는 저장된 필드를 조합하는 작업으로 LLM을 호출하지 않는다. 재실행으로 생긴 SQLite는 새 답변의 실험 기록이며, 운영 Wiki나 검색 DB에 병합하는 업데이트 파일이 아니다.
