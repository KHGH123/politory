"""열린국회정보 Open API(ALLNAMEMBER)에서 22대 이상 국회의원 인적사항을 수집해
BigQuery members 테이블(config.BIGQUERY_MEMBERS_TABLE)에 적재한다.

--inspect 실응답으로 필드명 검증 완료 (2026-08-21). ALLNAMEMBER는 대수별 행이 아니라
의원 1명당 1행이며, GTELT_ERACO에 "제9대, 제10대"처럼 재임한 모든 대수가 누적으로 들어있다.
명시적인 현직/전직 플래그 필드는 없다 - CMIT_NM/이메일 유무로 유추해봤으나 원내대표급
현역 의원도 이 필드가 비어있는 경우가 있어(예: 권성동) 신뢰할 수 없었다. 그래서 22대
재임 이력이 있으면 일단 "현직"으로 채운다(transform() 하단 주석 참고) - 임기 중
승계/제명으로 교체된 소수는 이 API만으로는 못 걸러낸다.

이 API로 채울 수 없는 필드(military, criminal, sns)는 NULL로 둔다.

사용법:
    python -m pipeline.collect_members --inspect            # 실제 응답 필드명 확인용, BigQuery 미적재
    python -m pipeline.collect_members --dry-run             # 22대 기준 변환 결과만 출력, BigQuery 미적재
    python -m pipeline.collect_members --dry-run --dae-num 21  # 21대 이상 기준으로 미리보기
    python -m pipeline.collect_members                       # 수집 + BigQuery 적재 (기본 22대 이상)
    python -m pipeline.collect_members --dae-num 21          # 21대 이상 기준으로 적재
"""
import argparse
import re
from datetime import date, datetime

import httpx
from google.cloud import bigquery

from config import settings

API_URL = "https://open.assembly.go.kr/portal/openapi/ALLNAMEMBER"
MIN_DAE_NUM = 22
PAGE_SIZE = 100

_FIELD = {
    "member_code": "NAAS_CD",
    "name": "NAAS_NM",
    "birth_date": "BIRDY_DT",
    "gender": "NTR_DIV",
    "party": "PLPT_NM",
    "district": "ELECD_NM",
    "elect_type": "ELECD_DIV_NM",
    "committee": "CMIT_NM",
    "committee_fallback": "BLNG_CMIT_NM",
    "term_count_raw": "RLCT_DIV_NM",
    "email": "NAAS_EMAIL_ADDR",
    "dae_num": "GTELT_ERACO",
    "image_url": "NAAS_PIC",
}

SCHEMA = [
    bigquery.SchemaField("id", "INTEGER", mode="REQUIRED"),
    bigquery.SchemaField("assembly_member_code", "STRING"),  # 열린국회정보 고유코드(자연키). bills/votes 조인용으로 추가.
    bigquery.SchemaField("name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("age", "INTEGER"),
    bigquery.SchemaField("party", "STRING"),
    bigquery.SchemaField("gender", "STRING"),
    bigquery.SchemaField("image_url", "STRING"),
    bigquery.SchemaField("military", "STRING"),
    bigquery.SchemaField("criminal", "STRING"),
    bigquery.SchemaField("email", "STRING"),
    bigquery.SchemaField(
        "sns",
        "RECORD",
        mode="REPEATED",
        fields=[
            bigquery.SchemaField("platform", "STRING"),
            bigquery.SchemaField("url", "STRING"),
        ],
    ),
    bigquery.SchemaField("committee", "STRING"),
    bigquery.SchemaField("district", "STRING"),
    bigquery.SchemaField("term_count", "INTEGER"),
    bigquery.SchemaField("status", "STRING"),
]


def _pick(raw: dict, key: str) -> str | None:
    return raw.get(_FIELD[key]) or None


def _last_segment(text: str | None) -> str | None:
    """PLPT_NM/ELECD_NM 등은 여러 대수에 걸쳐 재임한 의원의 경우
    "무소속/더불어민주당"처럼 시간순으로 슬래시 연결돼 있다 - 가장 최근 값만 취한다."""
    if not text:
        return None
    return text.split("/")[-1].strip() or None


def _parse_age(birth_date: str | None) -> int | None:
    if not birth_date:
        return None
    digits = re.sub(r"\D", "", birth_date)
    if len(digits) < 8:
        return None
    try:
        birth = datetime.strptime(digits[:8], "%Y%m%d").date()
    except ValueError:
        return None
    today = date.today()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def _parse_term_count(text: str | None) -> int | None:
    if not text:
        return None
    if "초선" in text:
        return 1
    if "재선" in text:
        return 2
    match = re.search(r"(\d+)선", text)
    return int(match.group(1)) if match else None


def _max_era(raw_value: str | None) -> int | None:
    """GTELT_ERACO: "제9대, 제10대"처럼 재임한 모든 대수가 콤마로 누적돼 있다 -> 최댓값이 최근 대수."""
    if not raw_value:
        return None
    numbers = [int(n) for n in re.findall(r"\d+", str(raw_value))]
    return max(numbers) if numbers else None


def fetch_all(api_key: str) -> list[dict]:
    """ALLNAMEMBER 전 페이지를 가져온다 (역대 전체 - 22대 미만은 이후 filter_min_dae_num으로 제거)."""
    rows: list[dict] = []
    page = 1
    with httpx.Client(timeout=15) as client:
        while True:
            resp = client.get(
                API_URL,
                params={"KEY": api_key, "Type": "json", "pIndex": page, "pSize": PAGE_SIZE},
            )
            resp.raise_for_status()
            body = resp.json()

            if "RESULT" in body:
                # pIndex가 마지막 페이지를 넘어가면 INFO-200(데이터 없음)로 응답 - 정상 종료 신호.
                if body["RESULT"].get("CODE", "").startswith("INFO"):
                    break
                raise RuntimeError(f"API 오류: {body['RESULT']}")

            head_key = next(k for k in body if k != "RESULT")
            chunks = body[head_key]
            page_rows = next((c["row"] for c in chunks if "row" in c), [])
            if not page_rows:
                break

            rows.extend(page_rows)
            page += 1
    return rows


def filter_min_dae_num(raw_rows: list[dict], min_dae_num: int) -> list[dict]:
    """GTELT_ERACO가 "제헌"(1948년 제헌국회, 숫자 없음)처럼 숫자가 없는 경우 era가
    None이 되는데, 이걸 통과시키면 22대와 무관한 과거 의원이 섞여 들어간다 -> era를
    못 구하면 무조건 제외한다."""
    filtered = []
    for raw in raw_rows:
        era = _max_era(_pick(raw, "dae_num"))
        if era is None or era < min_dae_num:
            continue
        filtered.append(raw)
    return filtered


def transform(raw_rows: list[dict]) -> list[dict]:
    """ALLNAMEMBER는 의원 1명당 1행이라 별도 dedupe는 필요 없지만, NAAS_CD 기준으로
    한 번 더 방어적으로 dedupe한 뒤 스키마에 맞게 매핑한다."""
    by_code: dict[str, dict] = {}
    for raw in raw_rows:
        code = _pick(raw, "member_code") or _pick(raw, "name")
        by_code.setdefault(code, raw)

    out = []
    for i, (code, raw) in enumerate(sorted(by_code.items()), start=1):
        district = _last_segment(_pick(raw, "district"))
        elect_type = _last_segment(_pick(raw, "elect_type"))
        if not district and elect_type and "비례" in elect_type:
            district = elect_type

        out.append(
            {
                "id": i,
                "assembly_member_code": code,
                "name": _pick(raw, "name"),
                "age": _parse_age(_pick(raw, "birth_date")),
                "party": _last_segment(_pick(raw, "party")),
                "gender": _pick(raw, "gender"),
                "image_url": _pick(raw, "image_url"),
                "military": None,
                "criminal": None,
                "email": _pick(raw, "email"),
                "sns": [],
                # CMIT_NM(현재 소속 위원회)이 비어있는 현직 의원이 있다(예: 권성동, 원내대표급도 예외 아님).
                # BLNG_CMIT_NM(소속 위원회 이력)에는 값이 남아있는 경우가 많아 폴백으로 쓴다.
                # BLNG_CMIT_NM도 party/district처럼 여러 대수 이력이 "/"로 이어질 수 있어 마지막 값만 취한다.
                "committee": _pick(raw, "committee") or _last_segment(_pick(raw, "committee_fallback")),
                "district": district,
                "term_count": _parse_term_count(_pick(raw, "term_count_raw")),
                # ALLNAMEMBER엔 명시적 현직 플래그가 없고(getMemberCurrStateList도 폐기돼 대체 불가,
                # 2026-08-22 확인), CMIT_NM/이메일 유무로도 신뢰 있게 유추 못 한다(예: 권성동은
                # 현역인데도 비어있음). 그래서 "현직"이라 단정하는 대신, 실제로 검증 가능한 사실인
                # 최근 재임 대수만 보여준다 - GTELT_ERACO 최댓값(필터 통과 조건과 동일한 값).
                "status": f"제{_max_era(_pick(raw, 'dae_num'))}대",
            }
        )
    return out


def ensure_table(client: bigquery.Client, table_id: str) -> None:
    table = bigquery.Table(table_id, schema=SCHEMA)
    client.create_table(table, exists_ok=True)


def load(rows: list[dict]) -> None:
    client = bigquery.Client(project=settings.BIGQUERY_PROJECT)
    table_id = f"{settings.BIGQUERY_PROJECT}.{settings.BIGQUERY_DATASET}.{settings.BIGQUERY_MEMBERS_TABLE}"
    ensure_table(client, table_id)

    job_config = bigquery.LoadJobConfig(
        schema=SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    job = client.load_table_from_json(rows, table_id, job_config=job_config)
    job.result()
    print(f"{len(rows)}건 적재 완료 -> {table_id}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true", help="첫 페이지 raw 응답만 출력하고 종료 (필드명 검증용)")
    parser.add_argument("--dry-run", action="store_true", help="fetch+filter+transform까지만 하고 BigQuery 적재 없이 결과를 텍스트로 출력")
    parser.add_argument("--dae-num", type=int, default=MIN_DAE_NUM, help=f"몇 대 이상 의원을 수집할지 (기본 {MIN_DAE_NUM})")
    args = parser.parse_args()

    if args.inspect:
        import json

        with httpx.Client(timeout=15) as client:
            resp = client.get(
                API_URL,
                params={"KEY": settings.ASSEMBLY_API_KEY, "Type": "json", "pIndex": 1, "pSize": 3},
            )
        print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
        return

    raw_rows = fetch_all(settings.ASSEMBLY_API_KEY)
    raw_rows = filter_min_dae_num(raw_rows, args.dae_num)
    rows = transform(raw_rows)

    if args.dry_run:
        import json

        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        print(f"\n총 {len(rows)}건 (BigQuery 미적재, --dry-run)")
        return

    load(rows)


if __name__ == "__main__":
    main()
