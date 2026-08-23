#!/usr/bin/env python3
"""Build a canonical legislator master and evidence-based speaker mappings."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import ceil
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from google.cloud import bigquery, storage


API_BASE = "https://open.assembly.go.kr/portal/openapi"
MEMBER_ENDPOINT = "ALLNAMEMBER"
USER_AGENT = "Mozilla/5.0 (compatible; AssemblyIdentityNormalizer/1.0)"
IDENTITY_VERSION = "speaker-identity-v1"


def normalize_text(value: str | None) -> str:
    """API와 PDF 문자열을 비교 가능한 Unicode·공백 형태로 정규화한다."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", value).strip()


def is_legislative_role(position: str | None) -> bool:
    value = normalize_text(position)
    if value in {"위원", "의원", "의장", "부의장"}:
        return True
    if "국무위원" in value:
        return False
    return bool(
        re.fullmatch(
            r"(?:소)?위원장(?:대리|직무대리|직무대행)?"
            r"|.+위원장(?:대리|직무대리|직무대행)"
            r"|의장(?:대리|직무대리|직무대행)",
            value,
        )
    )


def identity_id(
    assembly_no: int,
    meeting_id: str,
    source_speaker_id: str | None,
    speaker_name: str,
    speaker_position: str | None,
) -> str:
    canonical = json.dumps(
        [
            IDENTITY_VERSION,
            assembly_no,
            meeting_id,
            source_speaker_id,
            speaker_name,
            speaker_position,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "si_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:48]


def fetch_member_page(api_key: str, page: int) -> tuple[list[dict[str, Any]], int, bytes]:
    """국회 공식 의원 API 한 페이지와 선언된 전체 건수를 가져온다."""
    params = {"KEY": api_key, "Type": "json", "pIndex": page, "pSize": 1000}
    request = Request(
        f"{API_BASE}/{MEMBER_ENDPOINT}?{urlencode(params)}",
        headers={"User-Agent": USER_AGENT},
    )
    with urlopen(request, timeout=90) as response:
        raw = response.read()
    payload = json.loads(raw)
    sections = payload.get(MEMBER_ENDPOINT)
    if not sections:
        raise RuntimeError(f"unexpected member API response: {payload}")
    head = sections[0].get("head", [])
    result = next((item["RESULT"] for item in head if "RESULT" in item), {})
    if result.get("CODE") != "INFO-000":
        raise RuntimeError(f"member API error: {result}")
    total = next((int(item["list_total_count"]) for item in head if "list_total_count" in item), 0)
    rows = sections[1].get("row", []) if len(sections) > 1 else []
    return rows, total, raw


def fetch_members(
    api_key: str, assembly_no: int
) -> tuple[list[dict[str, Any]], list[bytes]]:
    """의원 API 전체 페이지를 수집하고 지정 국회 대수의 의원만 반환한다."""
    first_rows, total, first_raw = fetch_member_page(api_key, 1)
    all_rows = list(first_rows)
    raw_pages = [first_raw]
    for page in range(2, ceil(total / 1000) + 1):
        rows, page_total, raw = fetch_member_page(api_key, page)
        if page_total != total:
            raise RuntimeError(f"member API total changed while paging: {total} -> {page_total}")
        all_rows.extend(rows)
        raw_pages.append(raw)
    if len(all_rows) != total:
        raise RuntimeError(f"member API row mismatch: declared={total}, received={len(all_rows)}")
    term = f"제{assembly_no}대"
    term_rows = [
        row
        for row in all_rows
        if term in [normalize_text(value) for value in str(row.get("GTELT_ERACO") or "").split(",")]
    ]
    return term_rows, raw_pages


def build_legislators(
    api_rows: list[dict[str, Any]], assembly_no: int, collected_at: str
) -> list[dict[str, Any]]:
    """공식 의원 코드로 안정적인 legislators 마스터 행을 생성한다."""
    result: list[dict[str, Any]] = []
    for row in api_rows:
        official_code = normalize_text(row.get("NAAS_CD") or row.get("MONA_CD"))
        name = normalize_text(row.get("NAAS_NM") or row.get("HG_NM"))
        name_hanja = normalize_text(row.get("NAAS_CH_NM") or row.get("HJ_NM"))
        if not official_code or not name:
            raise RuntimeError(f"member row missing MONA_CD or HG_NM: {row}")
        result.append(
            {
                "legislator_id": f"krna:{official_code}",
                "assembly_no": assembly_no,
                "official_member_code": official_code,
                "name": name,
                "party_name": normalize_text(row.get("PLPT_NM") or row.get("POLY_NM")) or None,
                "district": normalize_text(row.get("ELECD_NM") or row.get("ORIG_NM")) or None,
                "term_start": None,
                "term_end": None,
                "source": f"{API_BASE}/{MEMBER_ENDPOINT}",
                "source_updated_at": collected_at,
                "collected_at": collected_at,
                "_name_aliases": sorted({value for value in (name, name_hanja) if value}),
            }
        )
    ids = [row["legislator_id"] for row in result]
    codes = [row["official_member_code"] for row in result]
    if len(ids) != len(set(ids)) or len(codes) != len(set(codes)):
        raise RuntimeError("official member codes are not unique")
    return result


def build_identity_map(
    identity_rows: list[Any],
    legislators: list[dict[str, Any]],
    collected_at: str,
) -> list[dict[str, Any]]:
    """회의별 PDF 발언자 표기를 공식 의원 ID에 보수적으로 연결한다."""
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for member in legislators:
        for alias in member["_name_aliases"]:
            by_name[alias].append(member)

    # BigQuery groups raw strings before NFKC/whitespace normalization. Collapse
    # values that become the same canonical identity so UPDATE ... FROM remains
    # one-to-one and the deterministic identity ID stays unique.
    canonical_rows: dict[tuple[int, str, str | None, str, str | None], int] = defaultdict(int)
    for row in identity_rows:
        key = (
            int(row.assembly_no),
            str(row.meeting_id),
            str(row.source_speaker_id) if row.source_speaker_id else None,
            normalize_text(row.speaker_name),
            normalize_text(row.speaker_position) or None,
        )
        canonical_rows[key] += int(row.utterance_count)

    candidate_ids_by_source_id: dict[str, set[str]] = defaultdict(set)
    for _, _, source_speaker_id, speaker_name, _ in canonical_rows:
        candidates = by_name.get(speaker_name, [])
        if source_speaker_id and len(candidates) == 1:
            candidate_ids_by_source_id[source_speaker_id].add(candidates[0]["legislator_id"])

    result: list[dict[str, Any]] = []
    for key, utterance_count in canonical_rows.items():
        assembly_no, meeting_id, source_speaker_id, speaker_name, speaker_position = key
        candidates = by_name.get(speaker_name, [])
        legislator_id: str | None = None
        status = "UNRESOLVED"
        method = "INSUFFICIENT_EVIDENCE"
        confidence: float | None = None

        if source_speaker_id and len(candidate_ids_by_source_id[source_speaker_id]) > 1:
            status = "AMBIGUOUS"
            method = "SOURCE_ID_NAME_COLLISION"
        elif len(candidates) > 1:
            status = "AMBIGUOUS"
            method = "DUPLICATE_OFFICIAL_NAME"
        elif len(candidates) == 1 and source_speaker_id:
            legislator_id = candidates[0]["legislator_id"]
            status = "MATCHED"
            method = "EXACT_UNIQUE_NAME_WITH_SOURCE_ID"
            confidence = 0.99
        elif len(candidates) == 1 and is_legislative_role(speaker_position):
            legislator_id = candidates[0]["legislator_id"]
            status = "MATCHED"
            method = "EXACT_UNIQUE_NAME_WITH_LEGISLATIVE_ROLE"
            confidence = 0.90
        elif not candidates:
            method = "NO_CURRENT_OFFICIAL_MEMBER_MATCH"
        else:
            method = "NAME_MATCH_WITHOUT_LEGISLATIVE_EVIDENCE"

        result.append(
            {
                "speaker_identity_id": identity_id(
                    assembly_no,
                    meeting_id,
                    source_speaker_id,
                    speaker_name,
                    speaker_position,
                ),
                "assembly_no": assembly_no,
                "meeting_id": meeting_id,
                "source_speaker_id": source_speaker_id,
                "speaker_name": speaker_name,
                "speaker_position": speaker_position,
                "legislator_id": legislator_id,
                "resolution_status": status,
                "resolution_method": method,
                "confidence": confidence,
                "resolved_at": collected_at if status == "MATCHED" else None,
                "reviewed_at": None,
                "collected_at": collected_at,
                "_utterance_count": utterance_count,
            }
        )
    ids = [row["speaker_identity_id"] for row in result]
    if len(ids) != len(set(ids)):
        raise RuntimeError("speaker identity IDs are not unique")
    return result


def identity_source_query(project: str, dataset: str, assembly_no: int) -> str:
    """발언자 표기별 출현 횟수를 집계하는 BigQuery SQL을 만든다."""
    return f"""
      SELECT
        m.assembly_no,
        u.meeting_id,
        u.source_speaker_id,
        u.speaker_name,
        u.speaker_position,
        COUNT(*) AS utterance_count
      FROM `{project}.{dataset}.utterances` AS u
      JOIN `{project}.{dataset}.meetings` AS m USING (meeting_id)
      WHERE m.assembly_no = {assembly_no}
      GROUP BY 1, 2, 3, 4, 5
      ORDER BY u.meeting_id, u.source_speaker_id, u.speaker_name, u.speaker_position
    """


def print_report(
    legislators: list[dict[str, Any]], identities: list[dict[str, Any]]
) -> None:
    name_counts = Counter(row["name"] for row in legislators)
    duplicates = sorted(name for name, count in name_counts.items() if count > 1)
    status_counts = Counter(row["resolution_status"] for row in identities)
    method_counts = Counter(row["resolution_method"] for row in identities)
    utterance_counts = Counter()
    for row in identities:
        utterance_counts[row["resolution_status"]] += row["_utterance_count"]

    print(f"official legislators: {len(legislators):,}")
    print(f"duplicate official names: {duplicates or 'none'}")
    print(f"speaker identities: {len(identities):,}")
    print("identity rows by status:")
    for key, value in sorted(status_counts.items()):
        print(f"  {key}: {value:,}")
    print("utterances by identity status:")
    for key, value in sorted(utterance_counts.items()):
        print(f"  {key}: {value:,}")
    print("identity rows by method:")
    for key, value in sorted(method_counts.items()):
        print(f"  {key}: {value:,}")

    unresolved_with_source = sorted(
        (
            row
            for row in identities
            if row["source_speaker_id"] and row["resolution_status"] != "MATCHED"
        ),
        key=lambda row: row["_utterance_count"],
        reverse=True,
    )
    print(f"unmatched source-ID identities: {len(unresolved_with_source):,}")
    for row in unresolved_with_source[:30]:
        print(
            "  "
            f"{row['speaker_name']} | source={row['source_speaker_id']} | "
            f"position={row['speaker_position']} | utterances={row['_utterance_count']} | "
            f"{row['resolution_status']}/{row['resolution_method']}"
        )


def table_schema(name: str) -> list[bigquery.SchemaField]:
    if name == "legislators":
        return [
            bigquery.SchemaField("legislator_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("assembly_no", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("official_member_code", "STRING"),
            bigquery.SchemaField("name", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("party_name", "STRING"),
            bigquery.SchemaField("district", "STRING"),
            bigquery.SchemaField("term_start", "DATE"),
            bigquery.SchemaField("term_end", "DATE"),
            bigquery.SchemaField("source", "STRING"),
            bigquery.SchemaField("source_updated_at", "TIMESTAMP"),
            bigquery.SchemaField("collected_at", "TIMESTAMP", mode="REQUIRED"),
        ]
    if name == "legislator_terms":
        return [
            bigquery.SchemaField("term_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("legislator_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("assembly_no", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("name", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("party_name", "STRING"),
            bigquery.SchemaField("district", "STRING"),
            bigquery.SchemaField("term_start", "DATE"),
            bigquery.SchemaField("term_end", "DATE"),
            bigquery.SchemaField("source", "STRING"),
            bigquery.SchemaField("collected_at", "TIMESTAMP"),
        ]
    if name == "speaker_identity_map":
        return [
            bigquery.SchemaField("speaker_identity_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("assembly_no", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("meeting_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("source_speaker_id", "STRING"),
            bigquery.SchemaField("speaker_name", "STRING"),
            bigquery.SchemaField("speaker_position", "STRING"),
            bigquery.SchemaField("legislator_id", "STRING"),
            bigquery.SchemaField("resolution_status", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("resolution_method", "STRING"),
            bigquery.SchemaField("confidence", "FLOAT"),
            bigquery.SchemaField("resolved_at", "TIMESTAMP"),
            bigquery.SchemaField("reviewed_at", "TIMESTAMP"),
            bigquery.SchemaField("collected_at", "TIMESTAMP", mode="REQUIRED"),
        ]
    raise ValueError(name)


def publish_table(
    client: bigquery.Client,
    target: str,
    rows: list[dict[str, Any]],
    schema: list[bigquery.SchemaField],
) -> None:
    """정규화 테이블을 스테이징에 검증한 후 운영 테이블로 게시한다."""
    staging = target + "_staging"
    client.delete_table(staging, not_found_ok=True)
    client.create_table(bigquery.Table(staging, schema=schema))
    config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    client.load_table_from_json(rows, staging, job_config=config).result()
    loaded = client.get_table(staging).num_rows
    if loaded != len(rows):
        raise RuntimeError(f"staging row mismatch for {target}: {loaded} != {len(rows)}")
    copy_config = bigquery.CopyJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE
    )
    # Recreate the small identity tables from the validated staging schema.
    # Existing clustering can otherwise make a schema-compatible copy fail.
    client.delete_table(target, not_found_ok=True)
    client.copy_table(staging, target, job_config=copy_config).result()
    client.delete_table(staging)


def apply_updates(client: bigquery.Client, project: str, dataset: str) -> None:
    """MATCHED 매핑만 사용해 utterances.legislator_id를 갱신한다."""
    prefix = f"{project}.{dataset}"
    sql = f"""
      BEGIN TRANSACTION;

      UPDATE `{prefix}.utterances`
      SET legislator_id = NULL
      WHERE legislator_id IS NOT NULL;

      UPDATE `{prefix}.utterances` AS u
      SET legislator_id = i.legislator_id
      FROM `{prefix}.speaker_identity_map` AS i
      WHERE i.resolution_status = 'MATCHED'
        AND u.meeting_id = i.meeting_id
        AND COALESCE(u.source_speaker_id, '') = COALESCE(i.source_speaker_id, '')
        AND TRIM(REGEXP_REPLACE(NORMALIZE(COALESCE(u.speaker_name, ''), NFKC), r'\\s+', ' '))
            = COALESCE(i.speaker_name, '')
        AND TRIM(REGEXP_REPLACE(NORMALIZE(COALESCE(u.speaker_position, ''), NFKC), r'\\s+', ' '))
            = COALESCE(i.speaker_position, '');

      COMMIT TRANSACTION;
    """
    client.query(sql).result()


def validate_applied(client: bigquery.Client, project: str, dataset: str) -> dict[str, int]:
    """의원 ID 중복, 고아 연결 및 실제 적용 건수를 검사한다."""
    prefix = f"{project}.{dataset}"
    query = f"""
      WITH metrics AS (
        SELECT 'legislators' metric, COUNT(*) value FROM `{prefix}.legislators`
        UNION ALL SELECT 'duplicate_legislator_ids', COUNT(*) FROM (
          SELECT legislator_id FROM `{prefix}.legislators`
          GROUP BY legislator_id HAVING COUNT(*) > 1
        )
        UNION ALL SELECT 'identity_rows', COUNT(*) FROM `{prefix}.speaker_identity_map`
        UNION ALL SELECT 'matched_identity_rows', COUNTIF(resolution_status = 'MATCHED')
          FROM `{prefix}.speaker_identity_map`
        UNION ALL SELECT 'mapped_utterances', COUNTIF(legislator_id IS NOT NULL)
          FROM `{prefix}.utterances`
        UNION ALL SELECT 'orphan_utterance_legislator_ids', COUNT(*)
          FROM `{prefix}.utterances` u
          LEFT JOIN `{prefix}.legislators` l USING (legislator_id)
          WHERE u.legislator_id IS NOT NULL AND l.legislator_id IS NULL
        UNION ALL SELECT 'orphan_identity_legislator_ids', COUNT(*)
          FROM `{prefix}.speaker_identity_map` i
          LEFT JOIN `{prefix}.legislators` l USING (legislator_id)
          WHERE i.legislator_id IS NOT NULL AND l.legislator_id IS NULL
        UNION ALL SELECT 'matched_rows_without_legislator_id', COUNT(*)
          FROM `{prefix}.speaker_identity_map`
          WHERE resolution_status = 'MATCHED' AND legislator_id IS NULL
        UNION ALL SELECT 'unmatched_rows_with_legislator_id', COUNT(*)
          FROM `{prefix}.speaker_identity_map`
          WHERE resolution_status != 'MATCHED' AND legislator_id IS NOT NULL
      )
      SELECT metric, value FROM metrics ORDER BY metric
    """
    metrics = {row.metric: int(row.value) for row in client.query(query).result()}
    expected_zero = [
        "duplicate_legislator_ids",
        "orphan_utterance_legislator_ids",
        "orphan_identity_legislator_ids",
        "matched_rows_without_legislator_id",
        "unmatched_rows_with_legislator_id",
    ]
    failures = [name for name in expected_zero if metrics.get(name) != 0]
    if failures:
        raise RuntimeError(f"identity validation failed: {failures}; metrics={metrics}")
    return metrics


def parse_args() -> argparse.Namespace:
    """의원 정규화 대상 GCP 자원과 --apply 여부를 읽는다."""
    parser = argparse.ArgumentParser(description="Normalize Assembly legislator identities")
    parser.add_argument("--project", default="proj-aj04-211200020328")
    parser.add_argument("--dataset", default="assembly")
    parser.add_argument("--bucket", default="proj-aj04-211200020328-assembly-us")
    parser.add_argument("--assembly-no", type=int, default=22)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    """공식 명부 수집, 의원 테이블 생성, 발언자 매핑과 검증을 수행한다."""
    args = parse_args()
    api_key = os.environ.get("ASSEMBLY_API_KEY")
    if not api_key:
        raise SystemExit("set ASSEMBLY_API_KEY in the environment")
    collected_at = datetime.now(timezone.utc).isoformat()
    api_rows, raw_pages = fetch_members(api_key, args.assembly_no)
    legislators = build_legislators(api_rows, args.assembly_no, collected_at)

    client = bigquery.Client(project=args.project)
    identities_source = list(
        client.query(
            identity_source_query(args.project, args.dataset, args.assembly_no)
        ).result()
    )
    identities = build_identity_map(identities_source, legislators, collected_at)
    print_report(legislators, identities)
    if not args.apply:
        print("dry run only; no BigQuery or GCS changes made")
        return 0

    bucket = storage.Client(project=args.project).bucket(args.bucket)
    raw_prefix = f"raw/legislators/assembly_no={args.assembly_no}"
    for page, raw in enumerate(raw_pages, start=1):
        raw_path = f"{raw_prefix}/page={page:04d}.json"
        bucket.blob(raw_path).upload_from_string(raw, content_type="application/json")
    print(f"stored official API responses: gs://{args.bucket}/{raw_prefix}/")

    public_legislators = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in legislators
    ]

    public_identities = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in identities
    ]
    legislator_terms = [
        {
            "term_id": f"{row['legislator_id']}:assembly:{row['assembly_no']}",
            "legislator_id": row["legislator_id"],
            "assembly_no": row["assembly_no"],
            "name": row["name"],
            "party_name": row["party_name"],
            "district": row["district"],
            "term_start": row["term_start"],
            "term_end": row["term_end"],
            "source": row["source"],
            "collected_at": row["collected_at"],
        }
        for row in public_legislators
    ]
    publish_table(
        client,
        f"{args.project}.{args.dataset}.legislators",
        public_legislators,
        table_schema("legislators"),
    )
    publish_table(
        client,
        f"{args.project}.{args.dataset}.legislator_terms",
        legislator_terms,
        table_schema("legislator_terms"),
    )
    publish_table(
        client,
        f"{args.project}.{args.dataset}.speaker_identity_map",
        public_identities,
        table_schema("speaker_identity_map"),
    )
    apply_updates(client, args.project, args.dataset)
    metrics = validate_applied(client, args.project, args.dataset)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
