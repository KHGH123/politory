# pipeline/

외부 데이터 소스(열린국회정보 Open API 등)에서 데이터를 수집해 BigQuery에 적재하는 1회성/수동 실행 스크립트 모음.
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
3. BigQuery에 실제로 적재하려면 `gcloud auth application-default login` 등으로 GCP 인증이 되어 있어야 함.

## collect_members.py

열린국회정보 Open API(`ALLNAMEMBER`)에서 국회의원 인적사항을 수집해 BigQuery `members` 테이블에 적재한다.

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

## 앞으로 추가될 스크립트

법안(bills)/표결(votes)/발언(speeches) 수집 스크립트가 이후 이 폴더에 추가될 예정. 추가되면 이 README에도 사용법을 같이 정리한다.
