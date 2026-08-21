# agent 응답 출력 포맷 규칙 (v2, 제안 — JSON 구조화 출력)

## 배경 및 결정

agent(`agent/agent.py`의 `root_agent`)의 최종 응답을 순수 자연어 텍스트로 받아
백엔드가 정규식으로 파싱하는 방식(v1)을 검토했으나, 다음 이유로 **JSON 구조화
출력** 방식으로 변경 제안한다.

- `agent/subagent/router.py`가 이미 `output_schema=RouteDecision`(pydantic 모델)으로
  구조화 출력을 쓰고 있음 — 같은 패턴을 최종 응답에도 적용 가능.
- 자연어 텍스트에 날짜 프리픽스를 강제하는 방식은 LLM이 규칙을 매번 지킨다는
  보장이 없어 파싱이 불안정함. JSON은 `output_schema` 검증으로 형식이 보장됨.
- 가드레일("해석적 판단 금지") 검사도 필드 단위로 걸 수 있어 더 정확해짐.
- 타임라인 시각화가 목적이므로, 애초에 리스트 구조로 받는 게 자연스러움
  (텍스트로 냈다가 다시 구조로 파싱하는 왕복이 불필요).

## 제안 스키마

```json
{
  "answer": "종합하면 OO 의원은 해당 사안에 대해 여러 차례 위원회에서 의견을 밝혀왔다.",
  "timeline": [
    {
      "date": "2024-03-12",
      "source_type": "회의록",
      "trust": "primary",
      "content": "국토교통위원회에서 OO 의원은 \"...\" 라고 발언했다.",
      "source_url": null
    },
    {
      "date": "2024-05-02",
      "source_type": "뉴스",
      "trust": "secondary",
      "content": "한 언론 보도에 따르면 OO 의원은 관련 법안에 ...",
      "source_url": "https://example.com/news/123"
    }
  ]
}
```

### 필드 설명

| 필드 | 타입 | 설명 |
|---|---|---|
| `answer` | `string` | 자유 자연어 총평/요약. 시간순 판단 없이 종합 서술만. |
| `timeline` | `list[TimelineItem]` | 시간순 이벤트 목록. 비어 있을 수 있음. |
| `timeline[].date` | `string` | ISO 8601 (`YYYY-MM-DD`, 일자 불명확 시 `YYYY-MM`). |
| `timeline[].source_type` | `string` | `회의록` \| `뉴스` \| `법안` \| `표결` 등. |
| `timeline[].trust` | `"primary" \| "secondary"` | 1차(회의록/법안/표결 등 공식 기록) vs 2차(뉴스 등 보도). |
| `timeline[].content` | `string` | 해당 이벤트에 대한 자연어 서술. |
| `timeline[].source_url` | `string \| null` | 원문 링크. 없으면 `null`. |

### pydantic 모델 예시 (ADK `output_schema`용)

```python
from pydantic import BaseModel
from typing import Literal

class TimelineItem(BaseModel):
    date: str
    source_type: str
    trust: Literal["primary", "secondary"]
    content: str
    source_url: str | None = None

class AgentResponse(BaseModel):
    answer: str
    timeline: list[TimelineItem] = []
```

## 백엔드 처리 방식

- agent 응답을 그대로 `AgentResponse`로 파싱해 프론트에 전달 (추가 파싱 불필요).
- `timeline`이 비어 있으면 프론트는 `answer` 원문만 표시.
- agent가 스키마를 위반하면(예: pydantic validation 실패) mock으로 폴백.

## 상태

- **제안 단계**. `agent/agent.py`, `agent/subagent/summarizer.py` 등 최종 응답을
  만드는 지점에 `output_schema=AgentResponse`를 반영하는 건 agent 담당(C)과
  합의 후 진행한다.
- 합의 전까지 백엔드/프론트는 이 스키마를 따르는 mock 데이터로 시각화 로직을
  먼저 개발한다.

## 변경 이력

- v1: 자연어 텍스트 + `[YYYY-MM-DD|타입|신뢰도]` 프리픽스 파싱 방식 (폐기)
- v2: JSON 구조화 출력(`output_schema`) 방식으로 변경 (현재)
