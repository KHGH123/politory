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

이름+정책이 이미 구체적인 경우:

```json
{
  "sufficient": true,
  "member_name": "이재명",
  "legislator_id": "krna:IUD9392R",
  "keywords": [],
  "member_candidates": []
}
```

이름은 있지만 정책이 없는 경우 — 관련 키워드를 추천한다:

```json
{
  "sufficient": false,
  "member_name": null,
  "legislator_id": null,
  "keywords": [
    { "title": "검찰개혁", "reason": "검찰 제도 개선 관련 의정활동" },
    { "title": "사법제도", "reason": "사법 신뢰도 제고 관련 법안" }
  ],
  "member_candidates": []
}
```

동명이인이라 이름만으로 특정이 안 되는 경우 (예: "박지원 의원"):

```json
{
  "sufficient": false,
  "member_name": null,
  "legislator_id": null,
  "keywords": [],
  "member_candidates": [
    { "name": "박지원", "legislator_id": "krna:H7X3372O", "party": "더불어민주당", "district": "전북 군산시김제시부안군을", "image_url": "https://..." },
    { "name": "박지원", "legislator_id": "krna:8BF5855P", "party": "더불어민주당", "district": "전남 해남군완도군진도군", "image_url": "https://..." }
  ]
}
```

이름 없이 지역구만 입력한 경우 — BigQuery `mps.district` 컬럼과 대조해 후보를 좁힌다:

```json
{
  "sufficient": false,
  "member_name": null,
  "legislator_id": null,
  "keywords": [],
  "member_candidates": [
    { "name": "맹성규", "legislator_id": "krna:FJK3396E", "party": "더불어민주당", "district": "인천 남동구갑", "image_url": "https://..." }
  ]
}
```

이름·지역구 없이 정책/키워드만 입력한 경우 (예: "교통비 완화 정책") — LLM이 그 정책과 관련
있다고 아는 실존 의원 이름을 추천하면, 그 이름을 BigQuery `mps` 테이블로 검증해(0건=환각,
2건 이상=동명이인은 버림) 정확히 1명으로 특정되는 것만 후보로 남긴다. 후보가 하나도
검증되지 않으면 대신 키워드를 추천한다:

```json
{
  "sufficient": false,
  "member_name": null,
  "legislator_id": null,
  "keywords": [],
  "member_candidates": [
    { "name": "맹성규", "legislator_id": "krna:FJK3396E", "party": "더불어민주당", "district": "인천 남동구갑", "image_url": "https://..." }
  ]
}
```

존재하지 않는 인물을 지칭한 경우 (예: "홍길동 의원") — 이름을 지어내지 않고 정직하게 빈
상태로 돌아온다:

```json
{
  "sufficient": false,
  "member_name": null,
  "legislator_id": null,
  "keywords": [],
  "member_candidates": []
}
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| sufficient | boolean | O | 특정인+정책이 이미 구체적인지 |
| member_name | string \| null | X | 국회의원 DB에서 유일하게 특정된 이름만. 없거나 동명이인이면 `null` |
| legislator_id | string \| null | X | member_name이 유일하게 특정됐을 때만 채움 |
| keywords | array | O | `sufficient=false`이고 인물이 확정/역추천된 경우 정책 키워드 추천, 최대 3개 |
| keywords[].title | string | O | 키워드 |
| keywords[].reason | string | O | 추천 이유 |
| member_candidates | array | O | 동명이인 / 지역구 검색 / 정책 역추천 중 하나로 후보가 나온 경우 채움 |
| member_candidates[].name | string | O | |
| member_candidates[].legislator_id | string \| null | X | |
| member_candidates[].party | string \| null | X | |
| member_candidates[].district | string \| null | X | |
| member_candidates[].image_url | string \| null | X | |

> `member_candidates`가 채워지면 `keywords`는 항상 빈 배열이다(프론트가 `keywords`
> 존재 여부로 "동명이인" 라벨과 "관련 의원" 라벨을 구분하기 때문 — 동명이인/지역구
> 검색은 `keywords`가 원래 없고, 정책 역추천도 후보가 확정되면 `keywords`를 비운다).

---

## POST /api/query

### Request

```json
{
  "question": "이재명 의원 부동산 정책",
  "member_name": "이재명",
  "legislator_id": null,
  "party": null,
  "keyword": null
}
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| question | string | O | |
| member_name | string \| null | X | |
| legislator_id | string \| null | X | 동명이인 특정용. 화면2에서 후보 카드 선택 시 채워 보냄 |
| party | string \| null | X | 동명이인 특정용(legislator_id를 보조). 화면2에서 후보 카드 선택 시 채워 보냄 |
| keyword | string \| null | X | |

### Response `404`

`member_name`이 주어졌는데 국회의원 DB(BigQuery MP)에 없으면 검색 자체를 진행하지 않고 막는다.

```json
{ "detail": "등록된 국회의원이 아닙니다." }
```

### Response `200`

```json
{
  "answer": "이재명 의원은 부동산등기법 일부개정법률안 전자투표에서 찬성표를 던졌습니다[1].",
  "sources": [
    {
      "type": "primary",
      "title": "부동산등기법 일부개정법률안",
      "legislator_id": "krna:IUD9392R",
      "excerpt": "이재명 의원은 부동산등기법 일부개정법률안 전자투표에서 찬성하였다.",
      "description": "이재명 의원이 부동산등기법 개정안에 찬성함",
      "url": "https://record.assembly.go.kr/assembly/viewer/minutes/download/pdf.do?id=52242",
      "date": "2024-08-28",
      "page_start": 31,
      "page_end": 32
    },
    {
      "type": "secondary",
      "title": "국조실장, '李정부 부동산 정책 공약과 반대' 野 주장에 \"크게 벗어나지…",
      "legislator_id": null,
      "excerpt": null,
      "description": "국무조정실장이 부동산 정책의 대선 공약 기조 유지를 밝힘",
      "url": "https://www.newsis.com/view/NISX20260824_0003760593",
      "date": "2026-08-24",
      "page_start": null,
      "page_end": null
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

`sources[]`는 출처 종류(회의록/표결/뉴스)와 무관하게 같은 평평한 스키마 하나를 쓴다
(`agent/subagent/evidence_synthesis.py`의 `Source`를 그대로 재사용):

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| type | `"primary"` \| `"secondary"` | O | 1차(회의록 원문·법안·표결) / 2차(뉴스) |
| title | string | O | 회의명/법안명/기사 제목 |
| legislator_id | string \| null | X | 회의록·표결 근거일 때만. 뉴스 등 인물 ID가 없는 출처는 `null` |
| excerpt | string \| null | X | 원문에서 한 글자도 바꾸지 않고 그대로 옮긴 완결된 문장. 없으면 `null` |
| description | string \| null | X | excerpt와 별개로 LLM이 쓴 40자 내외 3인칭 요약 |
| url | string \| null | X | 원문 링크. 없으면 그 출처 자체가 답변에서 제거된다 |
| date | string \| null | X | |
| page_start | int \| null | X | 회의록 근거일 때 PDF 페이지 시작 |
| page_end | int \| null | X | 회의록 근거일 때 PDF 페이지 끝 |

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
