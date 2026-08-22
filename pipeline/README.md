# 국회 회의록 BigQuery·Vertex AI Search 파이프라인

공식 국회 회의록 PDF를 GCS에 보존하고, PDF에서 페이지·발언을 추출하여 BigQuery와
Vertex AI Search용 문서를 만드는 코드다. HTML 회의록 뷰어는 원문 수집에 사용하지 않는다.

## 운영 순서

```text
국회 Open API 메타데이터 + 공식 PDF 수집
→ PDF 페이지·발언 추출
→ 의원 ID 연결
→ Vertex AI Search 문서 생성
→ 전체 무결성 검증
→ 기존 Data Store에서 FULL Import
```

## 파일 설명

| 파일 | 역할 |
|---|---|
| `collect_assembly.py` | 연도·기간별 회의 메타데이터, 안건, 공식 PDF를 GCS와 BigQuery에 누적한다. HTML 경로는 실행되지 않으며 API 페이지네이션과 재실행 중복 방지를 지원한다. |
| `rebuild_pdf_tables.py` | GCS 공식 PDF 전체를 `pdftotext -raw`로 읽어 `pdf_pages`, `utterances` 스테이징 테이블을 만든다. 검증 후에만 운영 테이블로 교체한다. |
| `normalize_legislators.py` | 국회 공식 의원 정보에서 `legislators`, `legislator_terms`, `speaker_identity_map`을 만들고 발언에 `legislator_id`를 연결한다. |
| `build_search_documents.py` | 전체 `utterances`를 Vertex AI Search 입력 형식인 `search_documents(id, jsonData)`로 재생성한다. |
| `validate_search_documents.py` | 검색 문서 ID, JSON, 원본 발언 연결, 해시, 페이지·안건 연결 및 전체 발언 포함 여부를 검사한다. |
| `audit_pdf_sources.py` | BigQuery 회의 메타데이터와 GCS PDF의 날짜·회차·위원회·페이지를 읽기 전용으로 대조한다. |
| `bigquery_schema.sql` | BigQuery 운영 테이블 스키마 정의다. |
| `BIGQUERY_MCP_HANDOFF.md` | 팀원이 BigQuery, Vertex AI Search, MCP를 사용할 때 필요한 테이블·검색·권한 설명이다. |

## 주요 GCP 자원

```text
BigQuery: proj-aj04-211200020328.assembly
GCS:      gs://proj-aj04-211200020328-assembly-us
Search:   proj-aj04-211200020328.assembly.search_documents
Location: US
```

## 중요한 운영 규칙

1. 공식 PDF만 회의록 원문으로 사용한다.
2. 새 연도는 기존 `meeting_id`를 보존하면서 누적한다.
3. `search_documents`는 일부 회의만 지정하지 말고 전체 발언으로 재생성한다.
4. BigQuery 갱신 후 기존 Vertex AI Search Data Store에서 `FULL` Import한다.
5. API 키와 로컬 가상환경은 Git에 올리지 않는다.
