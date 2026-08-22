#!/usr/bin/env python3
"""Read-only audit: verify every stored official PDF against meeting metadata."""

from __future__ import annotations

import io
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from google.cloud import bigquery, storage
from pypdf import PdfReader


PROJECT = "proj-aj04-211200020328"
DATASET = "assembly"


def compact(value: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value or "")


@dataclass(frozen=True)
class AuditResult:
    meeting_id: str
    ok: bool
    pages: int
    reason: str


def audit_one(row: object, storage_client: storage.Client) -> AuditResult:
    uri = row.raw_pdf_gcs_uri
    if not uri or not uri.startswith("gs://"):
        return AuditResult(row.meeting_id, False, 0, "missing PDF URI")
    bucket_name, blob_name = uri[5:].split("/", 1)
    try:
        raw = storage_client.bucket(bucket_name).blob(blob_name).download_as_bytes()
        if not raw.startswith(b"%PDF"):
            return AuditResult(row.meeting_id, False, 0, "not a PDF")
        reader = PdfReader(io.BytesIO(raw), strict=False)
        pages = len(reader.pages)
        first_text = "\n".join(
            (reader.pages[index].extract_text() or "")
            for index in range(min(2, pages))
        )
    except Exception as exc:  # report every damaged/unreadable source
        return AuditResult(row.meeting_id, False, 0, f"read error: {exc}")

    normalized = compact(first_text)
    meeting_date = row.meeting_date
    date_token = f"{meeting_date.year}년{meeting_date.month}월{meeting_date.day}일"
    failures: list[str] = []
    if compact(date_token) not in normalized:
        failures.append(f"date {date_token}")
    if row.session_no and compact(f"제{row.session_no}회") not in normalized:
        failures.append(f"session {row.session_no}")

    if row.meeting_type == "plenary":
        if "국회본회의" not in normalized:
            failures.append("plenary title")
    elif row.committee_name:
        expected = compact(row.committee_name)
        # PDF headings insert '회의록 제N호' between a parent committee and
        # subcommittee name, so compare after removing that heading marker.
        comparable = re.sub(r"회의록제?\d*호?", "", normalized)
        if expected not in comparable:
            failures.append("committee title")

    if pages < 1:
        failures.append("zero pages")
    return AuditResult(row.meeting_id, not failures, pages, ", ".join(failures))


def main() -> int:
    bq = bigquery.Client(project=PROJECT)
    rows = list(
        bq.query(
            f"""
            SELECT meeting_id, meeting_type, committee_name, meeting_date,
                   session_no, meeting_no, raw_pdf_gcs_uri
            FROM `{PROJECT}.{DATASET}.meetings`
            ORDER BY meeting_date, meeting_id
            """
        ).result()
    )
    storage_client = storage.Client(project=PROJECT)
    results: list[AuditResult] = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(audit_one, row, storage_client) for row in rows]
        for completed, future in enumerate(as_completed(futures), start=1):
            results.append(future.result())
            if completed % 50 == 0 or completed == len(futures):
                print(f"audited {completed}/{len(futures)}", flush=True)

    failures = sorted((item for item in results if not item.ok), key=lambda x: x.meeting_id)
    print(f"meetings: {len(results)}")
    print(f"matched PDFs: {len(results) - len(failures)}")
    print(f"failed PDFs: {len(failures)}")
    print(f"total pages: {sum(item.pages for item in results):,}")
    for item in failures:
        print(f"FAIL {item.meeting_id}: {item.reason}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
