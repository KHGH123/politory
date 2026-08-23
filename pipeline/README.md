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

## 처음 한 번 준비

필요한 것은 Python 3, Google Cloud CLI, Poppler의 `pdftotext`다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
brew install poppler
gcloud auth application-default login
gcloud config set project proj-aj04-211200020328
export ASSEMBLY_API_KEY='발급받은_API_KEY'
```

이 코드는 ADC(Application Default Credentials)를 사용하므로 일반 `gcloud auth login`과
별도로 `gcloud auth application-default login`이 필요하다. API 키는 Git에 커밋하지 않는다.

새 프로젝트에서 처음 구축할 때만 데이터셋과 빈 테이블을 만든다.

```bash
bq --location=US mk --dataset proj-aj04-211200020328:assembly
bq query --use_legacy_sql=false < bigquery_schema.sql
```

이미 현재 BigQuery 테이블이 존재한다면 이 초기화 명령은 실행하지 않는다.

### 실행 전 체크리스트

`ASSEMBLY_API_KEY` 환경변수만 설정한다고 실행되는 것은 아니다. 아래 조건이 모두
필요하다.

```text
[ ] Python 가상환경과 requirements.txt 패키지 설치
[ ] pdftotext(Poppler) 설치
[ ] ASSEMBLY_API_KEY 환경변수 설정
[ ] GCP ADC 인증 설정
[ ] 실행 계정의 BigQuery·GCS 쓰기 권한
[ ] assembly 데이터셋과 bigquery_schema.sql 테이블 생성
```

현재 프로젝트에서 로컬로 실행할 때는 다음 네 가지를 확인하면 된다.

```bash
source .venv/bin/activate
export ASSEMBLY_API_KEY='발급받은_API_KEY'
gcloud auth application-default login
gcloud config set project proj-aj04-211200020328
```

확인 명령:

```bash
test -n "$ASSEMBLY_API_KEY" && echo "API key configured"
gcloud auth application-default print-access-token >/dev/null && echo "ADC configured"
command -v pdftotext
.venv/bin/python run_pipeline.py --skip-collect --dry-run
```

현재 사용자인 `aj04@iceu.kr`로 현재 프로젝트에서 실행한다면 기존 BigQuery 데이터셋,
GCS 버킷과 권한은 이미 준비되어 있다. 따라서 로컬에서는 가상환경 활성화, API 키 설정,
ADC 로그인 상태를 주로 확인하면 된다.

CI/CD에서는 `gcloud auth application-default login`을 실행하지 않는다. 배포 플랫폼에
연결된 서비스 계정 또는 Workload Identity를 ADC로 사용하고, 국회 API 키는 CI/CD의
Secret에 저장해 `ASSEMBLY_API_KEY` 환경변수로 주입한다. 서비스 계정에는 최소한 다음
권한이 필요하다.

```text
원본 프로젝트: roles/bigquery.jobUser
assembly 데이터셋: roles/bigquery.dataEditor
GCS 버킷: roles/storage.objectAdmin
```

서비스 계정 JSON 키 파일을 Git에 저장하지 않는다. `run_pipeline.py`는 데이터셋과 버킷을
자동 생성하지 않으므로 새 프로젝트에서는 위의 초기 생성 단계를 먼저 완료해야 한다.

## 전체 파이프라인 실행

전체 과정은 `run_pipeline.py` 하나로 실행하는 것을 권장한다. 예를 들어 2024년
제22대 개원일부터 2026년 8월 12일까지 수집하고 전체 DB를 재생성하려면 다음과 같다.

```bash
.venv/bin/python run_pipeline.py \
  --years 2024 2025 2026 \
  --first-year-start 2024-05-30 \
  --last-date 2026-08-12
```

이미 GCS와 BigQuery에 PDF 수집이 끝났고 파생 테이블만 다시 만들 때는 다음과 같다.

```bash
.venv/bin/python run_pipeline.py --skip-collect
```

실행될 명령만 확인하고 아무것도 변경하지 않으려면 `--dry-run`을 추가한다.
각 단계가 성공해야 다음 단계로 진행하며, 하나라도 실패하면 즉시 중단한다. 이 프로그램은
두 Search 문서를 모두 검증하지만 Vertex AI Search Data Store의 `FULL Import`는 자동으로
실행하지 않는다.

### 날짜 옵션의 정확한 의미

| 옵션 | 의미 |
|---|---|
| `--years 2026` | 별도 시작일이 없으면 `2026-01-01`부터 수집한다. |
| `--first-year-start 2026-08-13` | 첫 번째 연도의 수집 시작일이다. “이 날짜 이후”가 아니라 이 날짜를 포함한다. |
| `--last-date 2026-08-23` | 마지막 연도의 수집 종료일이다. “이 날짜 이후”가 아니라 이 날짜까지 포함한다. |
| `--skip-collect` | API·PDF 수집만 생략하고 파생 테이블은 전체 재생성한다. |
| `--reprocess-existing` | 기존 회의도 삭제 후 다시 수집한다. 오류 복구 외에는 사용하지 않는다. |
| `--dry-run` | 실행할 명령만 출력하고 GCS·BigQuery를 변경하지 않는다. |

예를 들어 현재 DB가 2026년 8월 12일까지 있고 8월 13일부터 8월 23일까지 추가하려면:

```bash
.venv/bin/python run_pipeline.py \
  --years 2026 \
  --first-year-start 2026-08-13 \
  --last-date 2026-08-23
```

이 경우 수집 단계는 `2026-08-13 ~ 2026-08-23`만 조회한다. 해당 기간에 이미 수집된
`meeting_id`는 건너뛰고 신규 회의만 추가한다. 그러나 수집 이후 단계는 신규 데이터만
부분 처리하지 않는다. 기존 데이터와 신규 데이터를 합친 전체 원본을 기준으로 다음
파생 테이블을 다시 만든다.

```text
pdf_pages
utterances
legislators / legislator_terms / speaker_identity_map
search_documents
vote_search_documents
```

즉, **원본 회의와 PDF는 보존·누적하고 검색용 파생 테이블은 검증 후 전체 재생성**한다.
`--first-year-start` 없이 `--years 2026 --last-date 2026-08-23`만 지정하면 수집 범위는
`2026-01-01 ~ 2026-08-23`이다. 옵션 이름은 `--yers`가 아니라 `--years`다.

다음 명령을 저장소 루트에서 순서대로 실행한다. 아래 예시는 2025년 전체를 기존 DB에
추가하고 모든 파생 데이터를 갱신하는 경우다.

### 1. 국회 API 메타데이터와 공식 PDF 수집

```bash
.venv/bin/python step01_collect_assembly.py \
  --year 2025 \
  --start-date 2025-01-01 \
  --end-date 2025-12-31
```

국회 Open API에서 본회의·위원회 목록과 안건을 페이지네이션하여 가져온다. API 원응답과
공식 PDF는 GCS에 보존하고, `meetings`, `agendas`, `ingestion_documents`에 누적한다.
이미 완료된 `meeting_id`는 건너뛰므로 같은 기간을 재실행해도 중복되지 않는다. 기존
회의를 다시 받아야 할 때만 `--reprocess-existing`을 추가한다. 수집은 항상 PDF 전용이다.

### 2. PDF에서 페이지 원문과 발언 추출

선택적으로 쓰기 없는 시험 실행을 먼저 할 수 있다.

```bash
.venv/bin/python step02_rebuild_pdf_tables.py
```

실제 BigQuery 반영 명령은 다음과 같다.

```bash
.venv/bin/python step02_rebuild_pdf_tables.py --apply
```

`meetings.raw_pdf_gcs_uri`의 모든 공식 PDF를 GCS에서 읽어 `pdftotext -raw`로 추출한다.
스테이징에서 ID 중복, 빈 발언, 고아키, 페이지 범위를 검사하고, 전부 통과할 때만 하나의
BigQuery 트랜잭션으로 `pdf_pages`와 `utterances`를 교체한다. 새 기간을 추가한 후에는
전체 PDF를 기준으로 다시 실행한다. 한 회의만 복구할 때는 다음처럼 해당 회의만 교체한다.

```bash
.venv/bin/python step02_rebuild_pdf_tables.py --meeting-id committee:56333 --apply
```

### 3. 발언자와 공식 의원 ID 연결

```bash
.venv/bin/python step03_normalize_legislators.py --apply
```

국회 공식 제22대 의원 명부로 `legislators`, `legislator_terms`,
`speaker_identity_map`을 재생성하고,
확실히 일치한 경우에만 `utterances.legislator_id`를 채운다. 장관·증인·동명이인처럼
확정할 수 없는 발언자는 임의 연결하지 않고 `UNRESOLVED` 또는 `AMBIGUOUS`로 남긴다.

### 4. Vertex AI Search 입력 문서 전체 재생성

```bash
.venv/bin/python step04_build_search_documents.py
```

전체 `utterances`를 최대 1,800자 청크로 나누고 문맥·출처 메타데이터를 포함한
`search_documents(id, jsonData)`를 만든다. 월별 스테이징 작업이 모두 성공한 뒤에만
운영 테이블을 교체한다. 새 데이터 추가 시에도 이 테이블은 전체 재생성한다.

### 5. 최종 무결성 검사

```bash
.venv/bin/python step05_validate_search_documents.py
```

출력의 `status`가 반드시 `PASS`여야 한다. JSON, 문서 ID 중복, 전체 발언 커버리지,
청크를 합친 원문 복원, 해시, 회의·PDF·안건 연결을 검사한다. `FAIL`이면 Data Store로
가져오지 않는다.

### 5-1. 공식 전자투표 검색 문서 생성·검증

```bash
.venv/bin/python step06_build_vote_search_documents.py --apply
.venv/bin/python step07_validate_vote_search_documents.py
```

본회의 PDF 마지막의 `전자투표 찬반 의원 성명`에서 안건별 찬성·반대·기권 명단을
추출한다. PDF에 적힌 선택별 인원수와 추출 이름 수, 전체 투표 인원 합계가 모두 맞는
표결만 `vote_search_documents(id, jsonData)`에 게시한다. 의원 이름은 항상 보존하고,
기존 의원 마스터에서 유일하게 확인될 때만 `legislator_id`도 함께 기록한다.
바깥 `id`는 Vertex AI Search 제약에 맞춰 영문·숫자·밑줄·하이픈만 사용하고 63자 이하인지
검증한다. 내부 연결키인 `jsonData.vote_id`는 이 제한과 별도로 관리한다.

### 6. 기존 Vertex AI Search Data Store 갱신

BigQuery 변경은 기존 Data Store에 자동 반영되지 않는다. 검증이 `PASS`이면 기존
Data Store의 **Documents → Import data → BigQuery**에서 아래 테이블을 선택하고
**FULL Import**를 실행한다.

```text
proj-aj04-211200020328.assembly.search_documents
```

Data Store나 Search App을 새로 만들 필요는 없다. Import 문서 수를 아래 결과와 비교한다.

```sql
SELECT COUNT(*) AS document_count
FROM `proj-aj04-211200020328.assembly.search_documents`;
```

## 새 기간 추가 작업 요약

```text
step01_collect_assembly.py
  → step02_rebuild_pdf_tables.py --apply
  → step03_normalize_legislators.py --apply
  → step04_build_search_documents.py
  → step05_validate_search_documents.py (PASS 확인)
  → step06_build_vote_search_documents.py --apply
  → step07_validate_vote_search_documents.py (PASS 확인)
  → 기존 Data Store에서 FULL Import
```

동료가 별도로 만든 `mps` 테이블은 이 파이프라인이 읽거나 수정하거나 삭제하지 않는다.

## 파일 설명

| 파일 | 역할 |
|---|---|
| `run_pipeline.py` | 수집부터 두 검색 문서 검증까지 전체 프로그램을 올바른 순서로 실행하고 실패 시 중단한다. |
| `step01_collect_assembly.py` | 연도·기간별 회의 메타데이터, 안건, 공식 PDF를 GCS와 BigQuery에 누적한다. HTML 경로는 실행되지 않으며 API 페이지네이션과 재실행 중복 방지를 지원한다. |
| `step02_rebuild_pdf_tables.py` | GCS 공식 PDF 전체를 `pdftotext -raw`로 읽어 스테이징에서 검증한 뒤 `pdf_pages`, `utterances`를 트랜잭션으로 교체한다. |
| `step03_normalize_legislators.py` | 국회 공식 의원 정보에서 `legislators`, `legislator_terms`, `speaker_identity_map`을 만들고 발언에 `legislator_id`를 연결한다. |
| `step04_build_search_documents.py` | 전체 `utterances`를 Vertex AI Search 입력 형식인 `search_documents(id, jsonData)`로 재생성한다. |
| `step05_validate_search_documents.py` | 검색 문서 ID, JSON, 원본 발언 연결, 해시, 페이지·안건 연결 및 전체 발언 포함 여부를 검사한다. |
| `step06_build_vote_search_documents.py` | 본회의 PDF의 전자투표 찬반 명단을 검증하여 `vote_search_documents(id, jsonData)`를 만든다. |
| `step07_validate_vote_search_documents.py` | 표결 문서 JSON, ID, 인원 합계, 회의·PDF 연결을 검사한다. |
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

## 현재 적재 범위

```text
기간: 2024-05-30(제22대 개원) ~ 2026-08-12
회의: 1,803
PDF 페이지: 105,771
발언: 937,825
Vertex AI Search 문서: 952,612
```

`search_documents` 전체 무결성 검사는 2026-08-22 기준 `PASS`다.

## 중요한 운영 규칙

1. 공식 PDF만 회의록 원문으로 사용한다.
2. 새 연도는 기존 `meeting_id`를 보존하면서 누적한다.
3. `search_documents`는 일부 회의만 지정하지 말고 전체 발언으로 재생성한다.
4. BigQuery 갱신 후 기존 Vertex AI Search Data Store에서 `FULL` Import한다.
5. API 키와 로컬 가상환경은 Git에 올리지 않는다.
