# 신규 회의록 증분 파이프라인

## 현재 배포 상태

2026-08-25 기준으로 다음 리소스까지만 배포했다.

- Cloud Run Job: `assembly-incremental`
- 리전: `asia-northeast3`
- 이미지: `asia-northeast3-docker.pkg.dev/proj-aj04-211200020328/cloud-run-source-deploy/assembly-incremental:20260825-04`
- 이미지 digest: `sha256:3dbd24baea02e164601acce11de84686942b3458f3f324902c60ad1e535eb6a9`
- 서비스 계정: `assembly-incremental-job@proj-aj04-211200020328.iam.gserviceaccount.com`
- 모드: 드라이런 (`--apply` 없음)
- 실행 이력: 실제 증분 실행 성공 (`assembly-incremental-dpsxx`, 회의 9건·검색 문서 6,653건 반영)
- Scheduler: `assembly-incremental-daily`, 매일 06:00 (`Asia/Seoul`), 실행 override 권한 보완 필요

`assembly-api-key` Secret과 BigQuery/GCS 최소 권한을 연결했다. Job 기본값은 드라이런으로
유지하고 Scheduler 호출 본문에서만 `--apply`를 전달한다. Vertex 데이터 스토어 ID는
설정하지 않아 Vertex import는 실행하지 않는다.

국회 API에서 최근 회의를 조회하고, 신규 또는 이전에 실패한 `meeting_id`만 다시 처리하는
Cloud Run Job용 코드다. 기존 전체 구축 스크립트의 수집·PDF 파싱 로직은 재사용하고,
발언자 ID를 확정한 뒤 검색 문서를 생성한다. 처리 순서는 다음과 같다.

1. 최근 기간의 신규 회의를 국회 API에서 수집한다.
2. 대상 회의의 PDF 페이지·발언·안건을 회의 단위로 재구축한다.
3. 기존 `source_speaker_members` 대응표를 대상 회의에만 먼저 적용한다.
4. 그래도 미확정인 의원 발언만 공식 웹 회의록으로 단일 요청 검증한다.
5. ID가 반영된 발언으로 `search_documents`와 `vote_search_documents` delta를 만든다.
6. 설정된 Vertex 데이터 스토어에 delta만 `INCREMENTAL` import한다.
7. 전 단계가 성공한 회의만 `SUCCESS/COMPLETE`로 기록한다.

다음 테이블만 회의 단위로 갱신한다.

- `pdf_pages`, `utterances`
- `search_documents`
- `vote_search_documents` (신규 본회의에 전자투표가 있을 때)

`search_documents`와 `vote_search_documents`는 전체 `WRITE_TRUNCATE`를 사용하지 않는다.
선택된 `meeting_id` 행만 트랜잭션 안에서 삭제하고 새 delta를 삽입한다. 따라서 재시도해도
중복이 생기지 않는다.

## 안전한 기본값

아무 옵션 없이 실행하면 계획만 출력하고 GCP를 읽거나 수정하지 않는다.

```bash
python -m incremental_pipeline.main
python -m unittest incremental_pipeline.test_incremental
```

실제 반영은 명시적으로 `--apply`를 전달해야 한다.

```bash
ASSEMBLY_API_KEY=... python -m incremental_pipeline.main --apply
```

기본 조회 범위는 실행일을 포함한 최근 7일이다. 날짜 범위가 연도를 넘으면 국회 수집기의
연도 제한에 맞춰 자동으로 두 번 실행한다.

국회 API 조회는 요청당 20초, 최대 3회만 재시도하고 실패 원인을 키가 제거된 로그로 남긴다.
PDF 다운로드는 파일 크기를 고려해 기존의 긴 제한시간을 사용한다. GCS 원본은 객체가 없을
때만 생성하므로 재시도 중 기존 원본을 덮어쓰거나 삭제하지 않는다.

## 컨테이너 빌드

저장소 루트에서 빌드한다. 현재 배포 이미지는 위의 `20260825-02` 태그다.

```bash
docker build -f incremental_pipeline/Dockerfile \
  -t asia-northeast3-docker.pkg.dev/PROJECT/REPOSITORY/assembly-incremental:latest .
```

## Cloud Run Job 설정

컨테이너 인자는 `--apply`로 설정하고 `ASSEMBLY_API_KEY`는 Secret Manager에서 환경변수로
주입한다. 다음 환경변수는 선택 사항이다.

최초 배포에서는 `--apply`를 넣지 않은 드라이런 Job으로 올릴 수 있다. API 키 Secret과
Vertex 데이터 스토어 ID, 서비스 계정 권한을 확인한 뒤에만 `--apply`를 추가한다.

| 환경변수 | 기본값/용도 |
|---|---|
| `GCP_PROJECT` | `proj-aj04-211200020328` |
| `BQ_DATASET` | `assembly` |
| `ASSEMBLY_BUCKET` | 원본 PDF GCS 버킷 |
| `VERTEX_SEARCH_DATA_STORE_ID` | 설정 시 발언 delta를 Vertex에 INCREMENTAL import |
| `VERTEX_VOTE_DATA_STORE_ID` | 설정 시 표결 delta를 Vertex에 INCREMENTAL import |
| `VERTEX_PROJECT` | 기본 `proj-aj36-211200020328` (Vertex 팀 프로젝트) |
| `VERTEX_SEARCH_LOCATION` | 기본 `global` |
| `SPEAKER_REQUEST_DELAY` | 공식 뷰어 회의 간 요청 간격, 기본 1.5초 |
| `SPEAKER_FETCH_ATTEMPTS` | 회의별 지수 백오프 재시도 횟수, 기본 5회 |
| `SPEAKER_MAX_CONSECUTIVE_FAILURES` | 연속 실패 회로 차단 기준, 기본 3개 회의 |

Vertex 데이터 스토어 ID가 없으면 BigQuery 게시까지만 수행한다. 두 ID가 설정된 경우에도
전체 테이블이 아니라 해당 실행에서 생성한 delta 테이블만 import한다.

## 발언자 ID 보정과 Vertex 재반영

PDF 발언의 의원 식별자는 루트의 `step08_enrich_speaker_ids.py`가 보정한다. 두 ID의 역할은
서로 다르다.

- `utterances.legislator_id`: 서비스에서 의원을 조회하는 기준 ID
- `utterances.source_speaker_id`: 국회 웹 회의록의 발언자 ID
- `source_speaker_members`: 두 ID 사이의 검증된 1:1 대응표
- `speaker_identity_evidence`: 회의 ID·일자·대수·위원회가 일치한 공식 HTML 근거

이미 검증된 대응표만 다시 전파할 때는 국회 웹사이트를 호출하지 않는다.

```bash
python -u step08_enrich_speaker_ids.py \
  --assembly-no 22 \
  --apply \
  --propagate-verified-links \
  --verified-links-only
```

이 명령은 기존 `legislator_id`를 덮어쓰지 않고, 22대 범위에서 비어 있는 ID만 채운다.
`search_documents`는 전체 재생성하지 않으며 기존 문서의 `source_speaker_id`,
`legislator_id`, `identity_status`만 동기화한다.

BigQuery 수정 후 Vertex 작업자는 `assembly.search_documents` 또는 실행별 delta 테이블을
기존 데이터 스토어에 `reconciliationMode=INCREMENTAL`로 import한다. 문서 `id` 컬럼을
그대로 사용하고 자동 ID 생성은 사용하지 않는다. `FULL` 모드는 소스에 없는 기존 문서를
삭제할 수 있으므로 이 갱신 작업에는 사용하지 않는다.

국회 공식 뷰어에서 새로운 발언자 ID를 수집할 때는 반드시 단일 요청 흐름을 사용한다.
기본값은 `--workers 1`, 요청 간격 1.5초, 회의별 최대 5회 지수 백오프 재시도다.
연속 3개 회의가 실패하면 회로 차단기가 이후 요청을 중단한다. 공식 뷰어는 병렬 요청에서
다른 회의를 반환하거나 HTTP 400을 반환한 이력이 있으므로 `--workers`를 1보다 크게
설정하면 프로그램이 실행을 거부한다.

증분 파이프라인은 `step08_enrich_speaker_ids.py`를 다음 원칙으로 호출한다.

- 신규·재시도 `meeting_id`만 전달한다.
- 기존 291명 대응표는 해당 회의에만 적용하고 전체 과거 테이블은 스캔하지 않는다.
- `--skip-search-documents`로 발언 ID만 먼저 보정한다.
- 이후 동일 실행에서 회의 delta 검색 문서를 한 번만 생성한다.
- 공식 회의 검증이 하나라도 실패하면 `--fail-on-rejected`에 의해 실행을 실패 처리한다.

권장 실행 설정:

- Cloud Run Job task 1개, parallelism 1
- task timeout 60분 이상
- retry 1회
- Cloud Scheduler 시간대 `Asia/Seoul`, 하루 1회

## 서비스 계정 권한

최소한 다음 리소스에 대한 읽기·쓰기 권한이 필요하다.

- 대상 BigQuery dataset
- 원본 PDF GCS bucket
- Vertex AI Search data store (Vertex import를 사용하는 경우)
- `ASSEMBLY_API_KEY` Secret 접근

## 실패와 재시도

`incremental_meeting_status` 테이블을 자동 생성한다. 신규 회의는 `PENDING`으로 기록되고
`pipeline_stage`가 `DISCOVERED → PDF_REBUILD → SPEAKER_IDENTITY → SEARCH_DOCUMENTS →
VERTEX_IMPORT → COMPLETE` 순서로 바뀐다. 모든 게시가 끝난 뒤에만 `SUCCESS`가 된다.
실패하면 마지막 단계와 함께 `FAILED`로 남으므로 다음 실행에서 날짜 범위를 벗어나더라도
다시 처리한다. 발언자 검증 실패 상태에서는 검색 문서와 Vertex import를 실행하지 않는다.

실행별 delta 테이블은 Vertex import가 끝난 뒤 삭제한다. 장애 조사 목적으로 남기려면
`--keep-delta-tables`를 사용한다.

## 의도적으로 하지 않는 작업

- 전체 원본 테이블 재구축
- 기존 `search_documents` 또는 `vote_search_documents` 전체 재생성
- `legislators` 마스터 자동 덮어쓰기
- 파이프라인 실행 중 컨테이너 빌드 또는 GCP 배포
- 파이프라인 실행 중 Cloud Scheduler 생성·수정
