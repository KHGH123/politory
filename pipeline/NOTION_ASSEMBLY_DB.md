# 국회 회의록·표결 검색 DB 스키마

최종 갱신: 2026-08-23

## 기본 정보

| 항목 | 값 |
|---|---|
| GCP 프로젝트 | `proj-aj04-211200020328` |
| BigQuery 데이터셋 | `assembly` |
| BigQuery 위치 | `US` |
| 발언 검색 테이블 | `assembly.search_documents` |
| 표결 검색 테이블 | `assembly.vote_search_documents` |
| 원본 PDF | `gs://proj-aj04-211200020328-assembly-us` |

---

## 1. `search_documents`

국회의원의 회의 발언을 Vertex AI Search에서 검색하기 위한 테이블이다.

```text
전체 경로: proj-aj04-211200020328.assembly.search_documents
문서 수: 952,612
```

### BigQuery 컬럼

| key | type | description |
|---|---|---|
| `id` | `STRING REQUIRED` | Vertex AI Search 문서 ID. 발언 ID와 청크 번호로 생성하며 중복이 없다. |
| `jsonData` | `STRING` | 아래 발언 본문·회의·발언자·PDF 근거 정보를 담은 JSON 문자열이다. |

### `jsonData` 내부 필드

| key | type | description |
|---|---|---|
| `schema_version` | `str` | 검색문서 생성 규칙 버전. 현재 `assembly-search-pdf-v2`. |
| `document_type` | `str` | 문서 종류. 항상 `assembly_utterance`. |
| `title` | `str` | 검색 결과 제목. `발언자 - 회의명` 형식. |
| `content` | `str` | PDF에서 추출한 실제 발언 청크. 최대 1,800자. |
| `retrieval_text` | `str` | 회의·날짜·위원회·안건·발언자 문맥을 포함한 검색용 문자열. |
| `meeting_id` | `str` | 회의 고유 ID. 예: `plenary:52103`, `committee:56333`. |
| `meeting_title` | `str` | 국회 API의 회의 제목. |
| `meeting_date` | `date(str)` | 회의 시작일. `YYYY-MM-DD`. |
| `meeting_type` | `str` | 회의 유형. `plenary` 또는 `committee`. |
| `committee_name` | `str/null` | 소관 위원회. 본회의이면 null일 수 있다. |
| `assembly_no` | `int` | 국회 대수. 현재 `22`. |
| `primary_utterance_id` | `str` | BigQuery `utterances`에서 전체 발언을 다시 조회하는 키. |
| `source_block_ids` | `ARRAY<str>` | 레거시 HTML 블록 ID. PDF 운영 문서는 빈 배열이다. |
| `sequence_no` | `int` | 해당 회의 안에서 발언이 나온 순서. |
| `chunk_index` | `int` | 긴 발언을 나눈 청크 번호. 1부터 시작한다. |
| `chunk_count` | `int` | 해당 발언의 전체 청크 수. |
| `char_start` | `int` | 전체 발언에서 이 청크가 시작하는 문자 위치. |
| `char_end` | `int` | 전체 발언에서 이 청크가 끝나는 문자 위치. |
| `speaker_name` | `str/null` | PDF에서 추출한 발언자 이름. |
| `speaker_label` | `str/null` | PDF 원문의 발언자 표시 전체. 이름과 직위가 함께 들어갈 수 있다. |
| `speaker_position` | `str/null` | 당시 표시 직위. 예: 의원, 위원장, 장관, 증인. |
| `source_speaker_id` | `str/null` | 원문에서 확보한 발언자 식별값. |
| `legislator_id` | `str/null` | 공식 의원 명부와 확실히 연결된 내부 ID. 예: `krna:57B28032`. |
| `identity_status` | `str` | 의원 ID 연결 상태. `MATCHED` 또는 `UNRESOLVED`. |
| `agenda_ids` | `ARRAY<str>` | 직접 연결이 확인된 안건 ID 목록. |
| `agenda_titles` | `ARRAY<str>` | 직접 연결이 확인된 안건명 목록. |
| `agenda_link_method` | `str/null` | 발언과 안건을 연결한 방법. |
| `agenda_scope` | `str` | 안건 연결 신뢰 범위. `DIRECT`, `RANGE`, `UNRESOLVED`. |
| `source_agenda_count` | `int` | 원본 회의에서 확인된 관련 안건 수. |
| `is_short_utterance` | `bool` | 짧은 발언인지 여부. 짧은 발언에는 앞뒤 문맥을 추가한다. |
| `context_before` | `str/null` | 짧은 발언의 직전 발언 문맥. |
| `context_after` | `str/null` | 짧은 발언의 직후 발언 문맥. |
| `source_anchor` | `str/null` | 원문 내부 위치 식별값. PDF에서는 없을 수 있다. |
| `page_start` | `int` | 발언이 시작되는 공식 PDF 페이지. |
| `page_end` | `int` | 발언이 끝나는 공식 PDF 페이지. |
| `source_html_gcs_uri` | `null` | 폐기된 HTML 경로. 운영 문서는 항상 null. |
| `source_pdf_gcs_uri` | `str` | GCS에 보존한 공식 PDF 경로. 원문 그라운딩에 사용한다. |
| `source_html_url` | `str/null` | 국회 회의록 공식 조회 페이지. |
| `source_pdf_url` | `str` | 국회가 제공한 공식 PDF URL. |
| `utterance_content_sha256` | `str` | 전체 발언 원문의 SHA-256 무결성 해시. |
| `chunk_content_sha256` | `str` | 현재 청크 내용의 SHA-256 무결성 해시. |
| `parser_version` | `str` | PDF 파서 버전. |

### 검색 예시

```text
“정태호 의원이 반도체 정책에 관해 어떤 발언을 했나?”
```

검색 결과에서는 다음 필드를 사용한다.

```text
speaker_name, legislator_id, content
meeting_date, committee_name
primary_utterance_id, page_start, page_end, source_pdf_gcs_uri
```

---

## 2. `vote_search_documents`

본회의 PDF 마지막의 `전자투표 찬반 의원 성명`을 기반으로 공식 표결을 검색하는
Vertex AI Search용 테이블이다.

```text
전체 경로: proj-aj04-211200020328.assembly.vote_search_documents
문서 수: 355,987
검증된 표결 안건: 1,596
```

### BigQuery 컬럼

| key | type | description |
|---|---|---|
| `id` | `STRING REQUIRED` | Vertex AI Search 표결 문서 ID. 중복이 없다. |
| `jsonData` | `STRING` | 아래 표결 안건·의원 선택·PDF 근거 정보를 담은 JSON 문자열이다. |

### `jsonData` 공통 필드

| key | type | description |
|---|---|---|
| `schema_version` | `str` | 표결 검색문서 생성 규칙 버전. 현재 `assembly-vote-pdf-v1`. |
| `document_type` | `str` | `assembly_vote_summary` 또는 `assembly_vote_member`. |
| `title` | `str` | 안건명 또는 `의원명 - 안건명`. |
| `content` | `str` | 자연어 검색에 사용할 표결 요약 또는 의원별 선택 문장. |
| `retrieval_text` | `str` | 표결일·의원·안건·선택을 포함한 검색용 문자열. |
| `vote_id` | `str` | 표결 안건 고유 ID. |
| `meeting_id` | `str` | 표결이 기록된 본회의 ID. |
| `meeting_date` | `date(str)` | 회의가 시작된 날짜. |
| `vote_date` | `date(str)` | PDF 해당 페이지에 표시된 실제 표결일. 다일 회의에서는 meeting_date와 다를 수 있다. |
| `meeting_type` | `str` | 현재 공식 전자투표 문서는 `plenary`. |
| `committee_name` | `str/null` | 본회의이므로 일반적으로 null. |
| `vote_title` | `str` | 어떤 법안·안건에 대한 표결인지 나타내는 공식 PDF 안건명. |
| `total_count` | `int` | 전체 투표 의원 수. |
| `yes_count` | `int` | 찬성 의원 수. |
| `no_count` | `int` | 반대 의원 수. |
| `abstain_count` | `int` | 기권 의원 수. |
| `page_start` | `int` | 표결 명단이 시작되는 공식 PDF 페이지. |
| `page_end` | `int` | 표결 명단이 끝나는 공식 PDF 페이지. |
| `source_pdf_gcs_uri` | `str` | GCS에 보존한 공식 PDF 원본 경로. |
| `source_text_sha256` | `str` | 해당 표결 원문 구간의 SHA-256 무결성 해시. |

### 의원별 표결 문서 전용 필드

`document_type = assembly_vote_member`일 때만 존재한다.

| key | type | description |
|---|---|---|
| `member_name` | `str` | 공식 PDF 찬성·반대·기권 명단에 적힌 의원 이름. |
| `legislator_id` | `str/null` | 기존 공식 의원 마스터와 유일하게 일치할 때 연결한 내부 ID. |
| `choice` | `str` | 표결 선택 코드. `YES`, `NO`, `ABSTAIN`. |
| `choice_ko` | `str` | 표결 선택 한글. `찬성`, `반대`, `기권`. |
| `identity_status` | `str` | 의원 ID 연결 상태. `MATCHED` 또는 `AMBIGUOUS`. |

### `choice` 값

| value | description |
|---|---|
| `YES` | 찬성 |
| `NO` | 반대 |
| `ABSTAIN` | 기권 |

### 저장 예시

```json
{
  "document_type": "assembly_vote_member",
  "member_name": "김재섭",
  "legislator_id": "krna:57B28032",
  "vote_title": "순직해병수사방해및사건은폐등의진상규명을위한특별검사의임명등에관한법률안",
  "choice": "NO",
  "choice_ko": "반대",
  "meeting_date": "2024-07-03",
  "vote_date": "2024-07-04",
  "page_start": 313,
  "page_end": 313
}
```

### 검색 예시

```text
“김재섭 의원은 순직해병 특검법 표결에서 어떻게 투표했나?”
```

검색 결과에서는 다음 필드를 사용한다.

```text
member_name, legislator_id, vote_title
choice, choice_ko, vote_date
page_start, page_end, source_pdf_gcs_uri
```

---

## 3. 사용 시 주의사항

| 항목 | 설명 |
|---|---|
| 발언과 표결 구분 | 회의 중 “찬성한다”라고 발언한 것과 공식 전자투표의 `YES`는 다른 데이터다. |
| 의원 ID | 이름이 공식 의원 명부와 유일하게 일치할 때만 `legislator_id`를 채운다. |
| 표결 정확성 | PDF의 투표 인원과 추출 명단 수가 정확히 일치한 표결만 저장했다. |
| 제외 데이터 | 인원수가 일치하지 않은 표결 66건은 잘못된 찬반 저장을 막기 위해 제외했다. |
| 근거 제시 | 답변에는 가능하면 회의일·표결일·PDF 페이지·원본 경로를 함께 제시한다. |
| 정치적 해석 | PDF에 없는 정치적 입장이나 가결·부결 결과는 임의로 추정하지 않는다. |

## 4. Vertex AI Search 연결

| Data Store 권장 이름 | BigQuery 테이블 | 문서 수 |
|---|---|---:|
| `assembly-speeches` | `proj-aj04-211200020328.assembly.search_documents` | 952,612 |
| `assembly-votes` | `proj-aj04-211200020328.assembly.vote_search_documents` | 355,987 |

두 테이블을 각각 **Structured Data** Data Store로 연결하고 문서 ID 필드는 `id`, 위치는
`global`을 사용한다.
