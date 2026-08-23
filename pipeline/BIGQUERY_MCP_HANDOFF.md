# Vertex AI Search 전달 문서

최종 갱신: 2026-08-23

## 전달할 BigQuery 정보

```text
원본 GCP 프로젝트: proj-aj04-211200020328
BigQuery 데이터셋: assembly
BigQuery 위치: US
Vertex AI Search 위치: global
```

Vertex AI Search에는 아래 두 테이블을 각각 별도 Data Store로 연결한다.

| 용도 | BigQuery 테이블 | 예상 문서 수 |
|---|---|---:|
| 국회의원 회의 발언 검색 | `proj-aj04-211200020328.assembly.search_documents` | 952,612 |
| 의원별 공식 표결 검색 | `proj-aj04-211200020328.assembly.vote_search_documents` | 355,987 |

두 테이블 모두 Vertex AI Search의 BigQuery document 형식이다.

```text
id       STRING REQUIRED  -- 중복 없는 문서 ID
jsonData STRING           -- 검색 본문과 메타데이터 JSON
```

## Data Store 만드는 방법

두 테이블에 대해 아래 과정을 한 번씩 실행한다.

1. 팀 프로젝트 `proj-aj36-211200020328`에서 Vertex AI Search를 연다.
2. **Data Store 만들기**를 선택한다.
3. Source는 **BigQuery**를 선택한다.
4. Data type은 **Structured data**를 선택한다.
5. BigQuery 테이블 경로에 위 표의 테이블 경로를 입력한다.
6. Schema는 **document**, 문서 ID는 `id`를 선택한다.
7. Data Store 위치는 `global`을 선택한다.
8. Import가 끝나면 문서 수가 위 표의 예상 수와 같은지 확인한다.

권장 이름:

```text
assembly-speeches   → search_documents
assembly-votes      → vote_search_documents
```

BigQuery 데이터가 갱신되면 Data Store를 새로 만들지 않는다. 기존 Data Store의
**Documents → Import data → BigQuery**에서 같은 테이블을 선택해 **FULL Import**한다.

## 검색 가능한 내용

### `assembly-speeches`

- 특정 의원이 어떤 주제로 발언했는지
- 발언 날짜, 위원회, 회의, PDF 페이지
- 발언 원문 청크와 `legislator_id`

주요 필드:

```text
content, retrieval_text, speaker_name, legislator_id
meeting_date, committee_name, meeting_id
primary_utterance_id, page_start, page_end, source_pdf_gcs_uri
```

### `assembly-votes`

- 특정 의원이 어떤 안건에 찬성·반대·기권했는지
- 안건별 전체 찬성·반대·기권 인원
- 실제 표결일과 PDF 근거 페이지

주요 필드:

```text
content, retrieval_text, vote_title
member_name, legislator_id, choice, choice_ko
vote_date, meeting_id, vote_id
yes_count, no_count, abstain_count
page_start, page_end, source_pdf_gcs_uri
```

`choice` 값:

```text
YES      찬성
NO       반대
ABSTAIN  기권
```

`meeting_date`는 회의 시작일이고 `vote_date`는 PDF 페이지에 표시된 실제 표결일이다.

## 필요한 권한

팀 프로젝트의 Vertex AI Search 서비스 에이전트:

```text
service-939325476315@gcp-sa-discoveryengine.iam.gserviceaccount.com
```

현재 아래 두 원본 테이블 모두 이 서비스 에이전트의
`roles/bigquery.dataViewer`가 설정되어 있다.

```text
proj-aj04-211200020328.assembly.search_documents
proj-aj04-211200020328.assembly.vote_search_documents
```

팀 프로젝트 관리자는 위 서비스 에이전트가 Import 작업을 실행할 수 있도록 팀 프로젝트에
`roles/bigquery.jobUser`와 `roles/bigquery.dataEditor`가 설정되어 있는지도 확인한다.

## MCP 권장 흐름

```text
사용자 질문
  → 발언 내용은 assembly-speeches에서 검색
  → 공식 찬반은 assembly-votes에서 검색
  → meeting_id와 PDF 페이지로 원문 확인
  → 발언과 공식 표결을 구분해서 답변
```

예를 들어 “김재섭 의원은 순직해병 특검법 표결에서 어떻게 투표했나?”라는 질문은
`assembly-votes`에서 검색하고 다음 필드를 근거로 사용한다.

```text
member_name, legislator_id, vote_title, choice_ko
vote_date, page_start, source_pdf_gcs_uri
```

주의사항:

- 회의 중 “찬성한다”라고 말한 발언과 공식 전자투표 결과는 구분한다.
- 공식 찬반 질문에는 `assembly-votes`의 `choice`를 사용한다.
- PDF에 명시되지 않은 정치적 입장은 추정하지 않는다.
- 결과에는 가능하면 안건명, 선택, 표결일, PDF 페이지를 함께 표시한다.

## 최종 확인

```text
assembly-speeches 문서 수: 952,612
assembly-votes 문서 수:    355,987
두 Data Store 모두 검색 가능
MCP 응답에 안건명·선택·날짜·PDF 페이지 포함
```
