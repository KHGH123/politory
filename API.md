# API 명세

Base URL: `http://localhost:8000` (또는 `.env`의 `CORS_ORIGINS`에 맞는 배포 주소)

| Method | Path | 설명 |
| --- | --- | --- |
| GET | `/health` | 상태 확인 |
| POST | `/api/classify` | 질문 충분성 판단 + 키워드 추천 |
| POST | `/api/query` | 의정활동 조회 (답변 + 출처 + 약력) |

---

## GET /health

### Response `200`

```json
{ "status": "ok" }
```

---

## POST /api/classify

### Request

```json
{
  "question": "이재명 의원 부동산 정책"
}
```

| 필드 | 타입 | 필수 |
| --- | --- | --- |
| question | string | O |

### Response `200`

```json
{
  "sufficient": true,
  "member_name": "이재명",
  "keywords": []
}
```

```json
{
  "sufficient": false,
  "member_name": null,
  "keywords": [
    { "title": "검찰개혁", "reason": "검찰 제도 개선 관련 의정활동" },
    { "title": "사법제도", "reason": "사법 신뢰도 제고 관련 법안" }
  ],
  "member_candidates": []
}
```

동명이인이라 이름만으로 특정이 안 되는 경우 (예: "김민수 의원"):

```json
{
  "sufficient": false,
  "member_name": null,
  "keywords": [],
  "member_candidates": [
    { "name": "김민수", "party": "더불어민주당", "image_url": "https://..." },
    { "name": "김민수", "party": "국민의힘", "image_url": "https://..." }
  ]
}
```

이름 없이 정책/키워드만 입력한 경우 (예: "부동산 정책") — 검색축은 인물이라, 그 정책을 다룬
의원을 법안/발언 데이터에서 반대로 찾아 추천한다:

```json
{
  "sufficient": false,
  "member_name": null,
  "keywords": [
    { "title": "부동산 세제", "reason": "관련 법안 다수 발의" }
  ],
  "member_candidates": [
    { "name": "홍길동", "party": "더불어민주당", "image_url": "https://..." }
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| sufficient | boolean | O | 특정인+정책이 이미 구체적인지 |
| member_name | string \| null | X | 국회의원 DB에서 유일하게 특정된 이름만. 없거나 동명이인이면 `null` |
| keywords | array | O | `sufficient=false`일 때 정책 키워드 추천, 최대 3개 |
| keywords[].title | string | O | 키워드 |
| keywords[].reason | string | O | 추천 이유 |
| member_candidates | array | O | 동명이인이거나, 이름 없이 정책만 입력해 관련 의원을 역추천한 경우 채움 |
| member_candidates[].name | string | O | |
| member_candidates[].party | string \| null | X | |
| member_candidates[].image_url | string \| null | X | |

> `member_candidates`의 정책 역추천은 법안/발언 데이터(다른 팀원이 수집 중)에서
> `bills.title`/`bills.proposer`, `speeches.content`/`speeches.speaker` 컬럼을 가정하고
> 키워드로 찾는다 — 실제 컬럼명 확정되면 `backend/main.py`의 `_find_members_by_topic()`만
> 맞추면 됨. `.env`에 `BIGQUERY_BILLS_TABLE`/`BIGQUERY_SPEECHES_TABLE` 설정 필요.

---

## POST /api/query

### Request

```json
{
  "question": "이재명 의원 부동산 정책",
  "member_name": "이재명",
  "party": null,
  "keyword": null
}
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| question | string | O | |
| member_name | string \| null | X | |
| party | string \| null | X | 동명이인 특정용. 화면2에서 후보 카드 선택 시 채워 보냄 |
| keyword | string \| null | X | |

### Response `404`

`member_name`이 주어졌는데 국회의원 DB(BigQuery MP)에 없으면 검색 자체를 진행하지 않고 막는다.

```json
{ "detail": "등록된 국회의원이 아닙니다." }
```

### Response `200`

```json
{
  "answer": "…",
  "sources": [
    {
      "category": "speech",
      "type": "primary",
      "meeting": "국토교통위원회 제412회",
      "quote": "…",
      "url": "https://...",
      "date": "2024-03-12"
    },
    {
      "category": "bill",
      "date": "2024-01-20",
      "title": "부동산 거래신고 등에 관한 법률 일부개정법률안",
      "proposer": "이재명",
      "url": "https://..."
    }
  ],
  "member_profile": {
    "name": "이재명",
    "age": 60,
    "party": "더불어민주당",
    "gender": "M",
    "image_url": "https://...",
    "military": "…",
    "criminal": "…",
    "committee": "…",
    "district": "…",
    "term_count": 2,
    "status": "현직",
    "sns": [
      { "platform": "instagram", "url": "https://..." }
    ]
  }
}
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| answer | string | O | 최종 답변 |
| sources | array | O | 출처 목록, 없으면 `[]` |
| member_profile | object \| null | X | `member_name`이 없을 때만 `null`. 있는데 DB에 없으면 `404` (아래 참고) |

`sources[]`는 `category`로 구분되는 두 형태 중 하나:

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| category | `"speech"` | O | |
| type | `"primary"` \| `"secondary"` | O | 1차(회의록 원문) / 2차(뉴스) |
| meeting | string \| null | X | |
| quote | string | O | |
| url | string \| null | X | |
| date | string \| null | X | |

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| category | `"bill"` | O | |
| date | string \| null | X | |
| title | string | O | |
| proposer | string \| null | X | |
| url | string \| null | X | |

`member_profile`:

| 필드 | 타입 |
| --- | --- |
| name | string |
| age | int \| null |
| party | string \| null |
| gender | string \| null |
| image_url | string \| null |
| military | string \| null |
| criminal | string \| null |
| committee | string \| null |
| district | string \| null |
| term_count | int \| null |
| status | string \| null |
| sns | `{platform, url}[]` |
