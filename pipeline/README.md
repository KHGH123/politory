# pipeline/

외부 데이터 소스(열린국회정보 Open API, 국회 공식 회의록 PDF 등)에서 데이터를 수집해
BigQuery/Vertex AI Search에 적재하는 1회성/수동 실행 스크립트 모음.
스케줄러·폴링은 스코프 밖 — 필요할 때 사람이 직접 실행한다.

모든 명령은 **레포 루트에서** 실행한다고 가정한다 (`config.py` 참고).

```bash
cd politory   # 레포 루트
python -m pipeline.<스크립트명> [옵션]
```

## 실행 전 준비

1. 루트에 `.env` 파일 필요 (`.env.example` 복사해서 채우기):
   - `ASSEMBLY_API_KEY` — 열린국회정보 Open API 키 (미발급 시 `sample`로 테스트 가능)
   - `BIGQUERY_PROJECT`, `BIGQUERY_DATASET`, `BIGQUERY_MEMBERS_TABLE` 등 — 실제 적재까지 하려면 필요. `--dry-run`/`--inspect`만 쓸 거면 없어도 됨.
2. 의존성 설치: `pip install -r requirements.txt` (레포 루트 기준)
3. BigQuery/GCS에 실제로 적재하려면 `gcloud auth application-default login` 등으로 GCP 인증이 되어 있어야 함.

## collect_members.py — 국회의원 인적사항

열린국회정보 Open API(`ALLNAMEMBER`)에서 국회의원 인적사항을 수집해 BigQuery `mps` 테이블에 적재한다.

```bash
# 1. 실제 API 응답 필드명 확인용 (첫 페이지 3건만, 적재 없음)
python -m pipeline.collect_members --inspect

# 2. 변환 결과를 BigQuery 적재 없이 텍스트(JSON)로 미리보기
python -m pipeline.collect_members --dry-run

# 3. 몇 대 이상 의원을 수집할지 지정 (기본값 22)
python -m pipeline.collect_members --dry-run --dae-num 21

# 4. 실제 수집 + BigQuery 적재
python -m pipeline.collect_members
python -m pipeline.collect_members --dae-num 21
```

한글이 깨져서 출력되면(Windows 콘솔) 아래처럼 UTF-8을 강제한다:

```bash
PYTHONIOENCODING=utf-8 python -m pipeline.collect_members --dry-run
```

**옵션 요약**

| 옵션 | 설명 |
|---|---|
| `--inspect` | 첫 페이지 raw API 응답만 출력하고 종료. 실제 필드명 확인용, 적재 안 함 |
| `--dry-run` | fetch → filter → transform까지만 하고 결과를 한 줄당 JSON으로 출력. BigQuery 적재 안 함 |
| `--dae-num N` | N대 이상 재임 이력이 있는 의원만 수집 (기본 22) |

**알려진 한계** (자세한 내용은 파일 상단 docstring/코드 내 주석 참고):
- `military`, `criminal`, `sns`는 이 API로 채울 수 없어 항상 비어있음
- `status`는 "현직" 여부를 확실히 판별할 API가 없어서(`getMemberCurrStateList`도 폐기 확인됨), 대신 검증 가능한 최근 재임 대수(`제22대` 등)를 담는다
- `committee`는 `CMIT_NM`이 비어있으면 `BLNG_CMIT_NM`으로 폴백하지만, 극소수(예: 조정식)는 둘 다 비어있어 `null`로 남는다

## 국회 회의록 BigQuery·Vertex AI Search 파이프라인

공식 국회 회의록 PDF를 GCS에 보존하고, PDF에서 페이지·발언을 추출하여 BigQuery와
Vertex AI Search용 문서를 만드는 스크립트 묶음. HTML 회의록 뷰어는 원문 수집에 사용하지 않는다.

### 운영 순서

```text
국회 Open API 메타데이터 + 공식 PDF 수집
→ PDF 페이지·발언 추출
→ 의원 ID 연결
→ Vertex AI Search 문서 생성
→ 전체 무결성 검증
→ 기존 Data Store에서 FULL Import
```

### 파일 설명

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

### 주요 GCP 자원

```text
BigQuery: proj-aj04-211200020328.assembly
GCS:      gs://proj-aj04-211200020328-assembly-us
Search:   proj-aj04-211200020328.assembly.search_documents
Location: US
```

### 중요한 운영 규칙

1. 공식 PDF만 회의록 원문으로 사용한다.
2. 새 연도는 기존 `meeting_id`를 보존하면서 누적한다.
3. `search_documents`는 일부 회의만 지정하지 말고 전체 발언으로 재생성한다.
4. BigQuery 갱신 후 기존 Vertex AI Search Data Store에서 `FULL` Import한다.
5. API 키와 로컬 가상환경은 Git에 올리지 않는다.
