# 국회 회의록 BigQuery 전달 문서

최종 갱신: 2026-08-21

## 1. 바로 사용할 정보

```text
원본 프로젝트: proj-aj04-211200020328
BigQuery 데이터셋: assembly
BigQuery 위치: US

팀 프로젝트: proj-aj36-211200020328
Vertex AI Search Data Store 위치: global

Vertex AI Search 입력 테이블:
proj-aj04-211200020328.assembly.search_documents
```

Data Store에는 `search_documents`만 연결한다. 이 테이블은 Google의
BigQuery document schema에 맞춘 두 컬럼으로 구성된다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | `STRING REQUIRED` | 중복 없는 검색문서 ID |
| `jsonData` | `STRING` | 발언 본문과 출처 메타데이터를 담은 JSON |

## 2. Data Store 생성 방법

팀 프로젝트 `proj-aj36-211200020328`에서 다음과 같이 생성한다.

1. Vertex AI Search/AI Applications에서 **Data Store 만들기**를 선택한다.
2. Source는 **BigQuery**를 선택한다.
3. Synchronization은 우선 **One time**을 선택한다.
4. Data type은 **Structured data**를 선택한다.
5. BigQuery 테이블에 아래 경로를 입력한다.

   ```text
   proj-aj04-211200020328.assembly.search_documents
   ```

6. Data schema는 **document**, 문서 ID 필드는 `id`를 사용한다.
7. Data Store 위치는 `global`을 선택한다.
8. Import 완료 후 문서 수가 `147,087`인지 확인한다.

공식 문서:

- [BigQuery에서 Data Store 생성](https://cloud.google.com/generative-ai-app-builder/docs/create-data-store-es#import-from-bigquery)
- [Structured data 준비](https://cloud.google.com/generative-ai-app-builder/docs/prepare-data#bigquery-structured)

## 3. 설정된 권한

팀 프로젝트의 Discovery Engine 서비스 에이전트는 다음 계정이다.

```text
service-939325476315@gcp-sa-discoveryengine.iam.gserviceaccount.com
```

현재 완료된 원본 프로젝트 권한은 다음과 같다.

- `aj36@iceu.kr`: 원본 프로젝트의 `roles/bigquery.jobUser`
- `aj36@iceu.kr`: `assembly` 데이터셋의 `READER`(BigQuery Data Viewer)
- 위 서비스 에이전트: 원본의 `assembly.search_documents` 테이블에만
  `roles/bigquery.dataViewer`

따라서 사람 계정은 원본 DB를 조회할 수 있고, Data Store Import 서비스 에이전트는
검색용 테이블만 읽을 수 있다. 서비스 에이전트에는 데이터셋 전체 권한을 부여하지 않았다.

팀 프로젝트 관리자(`proj-aj36-211200020328`)가 별도로 완료해야 하는 설정:

1. Discovery Engine API 활성화
2. 위 서비스 에이전트에 팀 프로젝트의 `roles/bigquery.jobUser` 부여
3. 위 서비스 에이전트에 팀 프로젝트의 `roles/bigquery.dataEditor` 부여
4. Data Store를 만들 사람 계정에 필요한 Discovery Engine 관리 권한 부여

2~3은 Google의 BigQuery 기반 Data Store 생성 문서에 명시된 Import 선행 권한이다.
이 역할들은 **팀 프로젝트에만** 부여하며, 원본 프로젝트에는 부여하지 않는다. 프로젝트
Owner/Editor를 부여할 필요는 없다.

## 4. DB 구조

```text
assembly
├── meetings                 383   회의 메타데이터와 공식 PDF 경로
├── ingestion_documents      383   PDF 수집·파싱·검증 이력
├── agendas               10,227   API가 제공한 회의 안건
├── pdf_pages             20,379   PDF 페이지별 추출 원문
├── utterances           142,730   발언자 단위 발언
├── legislators              320   정규화 의원 마스터
├── legislator_terms         320   제22대 정당·지역구 이력
├── speaker_identity_map   7,764   발언자와 의원 ID 연결 근거
└── search_documents      147,087  Vertex AI Search 입력 문서
```

관계는 다음과 같다.

```text
meetings.meeting_id
  ├── pdf_pages.meeting_id
  ├── agendas.meeting_id
  └── utterances.meeting_id
        └── utterances.legislator_id
              └── legislators.legislator_id

utterances.utterance_id
  └── search_documents.jsonData.primary_utterance_id
```

## 5. 원문과 가공 데이터

- 공식 원본: GCS의 `minutes.pdf`
- 페이지 원문: `pdf_pages.extracted_text`
- 발언 단위 가공문: `utterances.utterance_text`
- 검색용 청크: `search_documents.jsonData.content`

`utterances`의 주요 근거 필드는 다음과 같다.

| 필드 | 의미 |
|---|---|
| `speaker_name` | PDF에서 분리한 발언자 이름 |
| `speaker_position` | 위원, 위원장, 장관 등 당시 표시 직위 |
| `legislator_id` | 공식 의원 명부와 안전하게 연결된 내부 ID |
| `utterance_text` | 해당 발언자의 발언문 |
| `page_start`, `page_end` | PDF 근거 페이지 범위 |
| `source_pdf_gcs_uri` | 공식 PDF 원본 경로 |
| `content_sha256` | 발언문 무결성 해시 |

예시 조회:

```sql
SELECT
  meeting_date,
  committee_name,
  speaker_name,
  speaker_position,
  legislator_id,
  page_start,
  page_end,
  utterance_text
FROM `proj-aj04-211200020328.assembly.utterances`
WHERE speaker_name = '정태호'
ORDER BY meeting_date, meeting_id, sequence_no;
```

## 6. 의원 ID 원칙

내부 ID는 국회 공식 `NAAS_CD`를 기반으로 관리한다.

```text
legislator_id = krna:{NAAS_CD}
```

자동 연결 조건은 공식 제22대 명부에서 이름이 유일하고, PDF 직위가 `위원`, `의원`,
`위원장` 등 입법부 직위로 확인되는 경우다. 장관·공무원·증인에게는 의원 ID를
부여하지 않는다. 동명이인 `박지원` 등은 근거가 부족하면 `AMBIGUOUS`로 남긴다.

## 7. 검색문서 JSON 예시

```json
{
  "schema_version": "assembly-search-pdf-v2",
  "document_type": "assembly_utterance",
  "content": "의석을 정돈해 주시기 바랍니다.",
  "meeting_id": "committee:56333",
  "meeting_date": "2026-03-04",
  "speaker_name": "정태호",
  "speaker_position": "소위원장",
  "legislator_id": "krna:...",
  "primary_utterance_id": "committee:56333:utterance:1",
  "page_start": 1,
  "page_end": 1,
  "source_pdf_gcs_uri": "gs://.../confer_num=56333/minutes.pdf"
}
```

MCP는 일반 검색에는 Vertex AI Search를 사용하고, 특정 의원·기간·위원회 필터나 원문
검증이 필요할 때 BigQuery의 `utterances`, `meetings`, `legislators`를 조회하면 된다.

## 8. 검증 결과와 제한사항

- 공식 PDF: 383건, 20,379페이지
- PDF가 있는 회의의 메타데이터 커버리지: 383/383
- 발언: 142,730건
- 의원 ID 연결 발언: 89,453건
- Search 문서: 147,087건, ID 중복 0건
- 빈 발언·고아 회의키·잘못된 페이지 범위: 0건
- Search 문서 JSON 오류·청크 복원 오류·PDF 링크 오류: 0건

찬성·반대 또는 정책 입장은 원문 사실이 아니라 분석 결과이므로 DB의 확정 사실로
저장하지 않았다. 에이전트가 관련 발언을 검색한 뒤 근거 발언과 PDF 페이지를 함께
제시하면서 평가해야 한다.

## 9. Data Store 생성 후 스키마 설정

Import가 끝나면 Data Store의 **Schema** 탭에서 다음 필드 설정을 확인한다. 자동 감지가
되더라도 검색 품질과 필터 동작을 위해 직접 확인하는 것이 좋다.

| 필드 | Searchable | Indexable | Retrievable | 용도 |
|---|---:|---:|---:|---|
| `title` | 예 | 예 | 예 | 결과 제목, `title` key property 권장 |
| `content` | 예 | 아니어도 됨 | 예 | PDF에서 추출한 실제 발언 청크 |
| `retrieval_text` | 예 | 아니어도 됨 | 예 | 회의·발언자 문맥을 포함한 검색 텍스트 |
| `speaker_name` | 예 | 예 | 예 | 의원명 검색·필터 |
| `legislator_id` | 아니어도 됨 | 예 | 예 | 동명이인 없는 의원 필터 |
| `meeting_date` | 아니어도 됨 | 예 | 예 | 날짜 필터·표시 |
| `committee_name` | 예 | 예 | 예 | 위원회 검색·필터 |
| `meeting_type` | 아니어도 됨 | 예 | 예 | `plenary`/`committee` 필터 |
| `primary_utterance_id` | 아니어도 됨 | 예 | 예 | BigQuery 원문 재조회 키 |
| `page_start`, `page_end` | 아니어도 됨 | 예 | 예 | PDF 근거 페이지 |
| `source_pdf_url` | 아니어도 됨 | 아니어도 됨 | 예 | 사용자에게 제시할 공식 PDF |

Google 문서상 `searchable`은 자연어 검색 대상, `indexable`은 필터·정렬·부스팅 대상,
`retrievable`은 검색 결과로 돌려받을 필드다. 스키마 변경 후에는 재색인 완료를 기다린다.

- [필드 설정 설명](https://cloud.google.com/generative-ai-app-builder/docs/configure-field-settings)
- [스키마 제공 및 수정](https://cloud.google.com/generative-ai-app-builder/docs/provide-schema)

## 10. MCP에서 사용하는 권장 흐름

```text
사용자 질문
  ↓
의원명·주제·기간 추출
  ↓
legislators에서 의원명 → legislator_id 확인
  ↓
Vertex AI Search에서 관련 발언 의미 검색
  ↓
검색 결과의 primary_utterance_id 수집
  ↓
BigQuery utterances에서 발언 전체와 PDF 페이지 검증
  ↓
근거 발언·회의·날짜·PDF 페이지를 포함해 답변
```

Vertex AI Search만으로도 검색할 수 있지만, 최종 답변을 만들 때는 검색 결과의
`primary_utterance_id`를 BigQuery에서 다시 조회하는 방식을 권장한다. 검색 청크가 긴
발언의 일부일 수 있기 때문에 전체 발언과 출처를 다시 가져와야 문맥이 보존된다.

MCP 도구는 다음 다섯 개 정도면 충분하다.

```text
resolve_legislator(name)
search_speeches(query, legislator_id?, date_from?, date_to?, committee_name?)
get_utterances(utterance_ids[])
get_pdf_page(meeting_id, page_number)
list_agendas(meeting_id)
```

### `resolve_legislator`

```sql
SELECT legislator_id, name, party_name, district
FROM `proj-aj04-211200020328.assembly.legislators`
WHERE name = @name;
```

결과가 두 명 이상이면 사용자에게 정당·지역구를 확인하거나 `AMBIGUOUS`로 처리한다.

### `search_speeches`

Vertex AI Search App의 serving config에 자연어 질의를 보낸다. Google은 App 검색에
`engines.servingConfigs.search` 사용을 권장한다.

```http
POST https://discoveryengine.googleapis.com/v1/projects/proj-aj36-211200020328/locations/global/collections/default_collection/engines/{APP_ID}/servingConfigs/default_search:search
Authorization: Bearer {ACCESS_TOKEN}
Content-Type: application/json

{
  "query": "대미 투자 특별법에 관한 정태호 의원의 발언",
  "pageSize": 10
}
```

스키마에서 `legislator_id`, `meeting_date`, `committee_name`을 indexable로 설정한 뒤에는
필터를 함께 사용할 수 있다. 필터 문법과 지원 타입은 실제 생성된 Data Store 스키마를
기준으로 확인한다.

- [Serving config 검색 API](https://cloud.google.com/generative-ai-app-builder/docs/reference/rest/v1/projects.locations.dataStores.servingConfigs/search)
- [검색 예제](https://cloud.google.com/generative-ai-app-builder/docs/samples/genappbuilder-search)

### `get_utterances`

Vertex 결과에서 받은 `primary_utterance_id`로 전체 발언을 다시 조회한다.

```sql
SELECT
  u.utterance_id,
  u.speaker_name,
  u.speaker_position,
  u.legislator_id,
  u.utterance_text,
  u.page_start,
  u.page_end,
  u.source_pdf_gcs_uri,
  m.meeting_date,
  m.title AS meeting_title,
  m.committee_name,
  m.pdf_url
FROM `proj-aj04-211200020328.assembly.utterances` AS u
JOIN `proj-aj04-211200020328.assembly.meetings` AS m USING (meeting_id)
WHERE u.utterance_id IN UNNEST(@utterance_ids)
ORDER BY m.meeting_date, u.meeting_id, u.sequence_no;
```

### `get_pdf_page`

```sql
SELECT
  p.meeting_id,
  p.page_number,
  p.extracted_text,
  p.source_pdf_gcs_uri,
  p.content_sha256,
  m.pdf_url
FROM `proj-aj04-211200020328.assembly.pdf_pages` AS p
JOIN `proj-aj04-211200020328.assembly.meetings` AS m USING (meeting_id)
WHERE p.meeting_id = @meeting_id
  AND p.page_number = @page_number;
```

### 의원별 전체 발언 타임라인

```sql
SELECT
  u.meeting_date,
  u.committee_name,
  u.speaker_position,
  u.utterance_text,
  u.page_start,
  u.page_end,
  m.pdf_url
FROM `proj-aj04-211200020328.assembly.utterances` AS u
JOIN `proj-aj04-211200020328.assembly.meetings` AS m USING (meeting_id)
WHERE u.legislator_id = @legislator_id
  AND u.meeting_date BETWEEN @date_from AND @date_to
ORDER BY u.meeting_date, u.meeting_id, u.sequence_no;
```

## 11. MCP 응답 규칙

MCP 또는 에이전트가 최종 사용자에게 돌려주는 각 근거에는 최소한 다음 항목을 포함한다.

```json
{
  "utterance_id": "committee:56333:utterance:1",
  "speaker_name": "정태호",
  "legislator_id": "krna:...",
  "meeting_date": "2026-03-04",
  "meeting_title": "제22대 제432회 ...",
  "quote": "의석을 정돈해 주시기 바랍니다.",
  "page_start": 1,
  "page_end": 1,
  "source_pdf_url": "https://record.assembly.go.kr/.../pdf.do?id=56333"
}
```

운영 규칙:

1. `content` 또는 `utterance_text`에 없는 문장을 직접 인용하지 않는다.
2. 의원의 찬성·반대는 검색 결과 한 건으로 단정하지 않는다.
3. 입장 분류 시 근거 발언 ID와 회의 날짜를 함께 반환한다.
4. 서로 다른 시점의 입장이 다르면 각각의 근거를 제시하고 변화 여부는 별도 분석임을
   표시한다.
5. `identity_status != MATCHED`인 문서는 특정 의원의 발언이라고 확정하지 않는다.
6. 같은 `primary_utterance_id`의 여러 청크는 하나의 발언으로 중복 제거한다.

## 12. MCP 런타임 권한

Data Store Import 서비스 에이전트 권한과 MCP 실행 계정 권한은 별개다.

- Vertex AI Search 호출: 팀 프로젝트에서 MCP 런타임 서비스 계정에
  `roles/discoveryengine.user` 부여
- BigQuery 직접 조회: MCP 런타임 서비스 계정 이메일을 원본 프로젝트 관리자에게 전달
- 원본 프로젝트: 필요한 테이블 또는 데이터셋에 `roles/bigquery.dataViewer` 부여
- 쿼리 실행 프로젝트: `roles/bigquery.jobUser` 부여

현재 설정된 Discovery Engine 서비스 에이전트 권한은 **Data Store Import용**이다. MCP가
`utterances`나 `pdf_pages`를 SQL로 직접 읽으려면 MCP 런타임 서비스 계정 정보를 추가로
받아 최소 권한을 부여해야 한다.
