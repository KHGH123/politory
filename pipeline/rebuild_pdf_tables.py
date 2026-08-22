#!/usr/bin/env python3
"""Rebuild Assembly page, utterance, and agenda tables from official PDFs."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from typing import Any, Iterable

from google.cloud import bigquery, storage

from normalize_legislators import fetch_members


PROJECT = "proj-aj04-211200020328"
DATASET = "assembly"
PARSER_VERSION = "pdf-pdftotext-raw-v1"
SPEAKER_MARK = re.compile(r"[◯○]")
PAGE_HEADER = re.compile(r"^\s*(?:\d+\s+)?제\s*\d+회\s*[-－].*?\d+\s*$")
FIRST_HEADER = re.compile(r"(?m)^\s*제\s*\d+회\s*국회")
ROLE = (
    r"위원장대리|소위원장|부위원장|위원장|국회의장|부의장|의장|위원|의원|간사|"
    r"국무위원|수석전문위원|전문위원|감사원장|대법원장|헌법재판소장|"
    r"장관|차관|처장|청장|원장|총장|실장|국장|과장|대표|사장|이사장|"
    r"본부장|단장|감사|후보자|증인|참고인"
)
ROLE_FIRST = re.compile(rf"^(.{{0,35}}?(?:{ROLE}))\s+([가-힣·]{{2,7}})\s*(.*)$")
NAME_FIRST = re.compile(rf"^([가-힣·]{{2,7}})\s+((?:{ROLE}))\s*(.*)$")
LEGISLATIVE_ROLE = re.compile(r"^(?:(?:소|부)?위원장(?:대리)?|국회의장|의장|부의장|위원|의원|간사)$")


def normalize(value: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip()


def sha256(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def extract_pages(pdf: bytes) -> list[str]:
    process = subprocess.run(
        ["pdftotext", "-raw", "-", "-"],
        input=pdf,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        raise RuntimeError(process.stderr.decode("utf-8", "replace"))
    text = process.stdout.decode("utf-8", "replace")
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


def clean_page_for_speeches(text: str, page_number: int) -> str:
    lines = [line for line in text.splitlines() if not PAGE_HEADER.match(line)]
    text = "\n".join(lines)
    if page_number == 1:
        first_speaker = SPEAKER_MARK.search(text)
        header = FIRST_HEADER.search(text)
        if first_speaker and header and header.start() > first_speaker.start():
            text = text[: header.start()]
        elif first_speaker:
            text = text[first_speaker.start() :]
    return text


def unwrap(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    paragraphs: list[str] = []
    current = ""
    for line in lines:
        if line:
            separator = "\n" if current and current[-1] in ".?!。？！…" else ""
            current += separator + line
        elif current:
            paragraphs.append(current)
            current = ""
    if current:
        paragraphs.append("".join(current))
    return "\n".join(paragraphs).strip()


def member_aliases(api_rows: list[dict[str, Any]]) -> tuple[list[str], dict[str, str]]:
    alias_to_id: dict[str, str] = {}
    for row in api_rows:
        code = normalize(row.get("NAAS_CD") or row.get("MONA_CD"))
        for field in ("NAAS_NM", "NAAS_CH_NM", "HG_NM", "HJ_NM"):
            alias = normalize(row.get(field))
            if alias and code:
                alias_to_id[alias] = f"krna:{code}"
    return sorted(alias_to_id, key=lambda value: (-len(value), value)), alias_to_id


def parse_speaker_line(
    line: str, aliases: list[str], alias_to_id: dict[str, str]
) -> tuple[str, str | None, str | None, str | None, str]:
    line = normalize(line)
    # Official names must win over the generic role/name pattern. Otherwise a
    # line such as "박수영 위원 야당 간사..." is misread as position
    # "박수영 위원" and person "야당".
    matches = [(line.find(alias), alias) for alias in aliases if 0 <= line.find(alias) <= 35]
    if matches:
        offset, name = min(matches, key=lambda item: (item[0], -len(item[1])))
        prefix = normalize(line[:offset])
        suffix = line[offset + len(name) :].lstrip()
        if prefix and len(prefix) <= 35:
            position, content = prefix, suffix
        else:
            role = re.match(rf"^({ROLE})\s*(.*)$", suffix)
            position = normalize(role.group(1)) if role else None
            content = role.group(2).strip() if role else suffix
        legislator_id = alias_to_id.get(name) if position and LEGISLATIVE_ROLE.fullmatch(position) else None
        return normalize(f"{position or ''} {name}"), name, position, legislator_id, content

    role_first = ROLE_FIRST.match(line)
    if role_first:
        position, name, content = role_first.groups()
        position = normalize(position)
        name = normalize(name)
        return normalize(f"{position} {name}"), name, position, None, content.strip()
    name_first = NAME_FIRST.match(line)
    if name_first:
        name, position, content = name_first.groups()
        position = normalize(position)
        name = normalize(name)
        return normalize(f"{name} {position}"), name, position, None, content.strip()

    # Preserve the complete segment even when a non-member label cannot be split.
    return line[:80], None, None, None, line


def parse_utterances(
    meeting: Any,
    pages: list[str],
    aliases: list[str],
    alias_to_id: dict[str, str],
    collected_at: str,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def append_text(value: str, page_number: int) -> None:
        nonlocal current
        value = unwrap(value)
        if current and value:
            current["_parts"].append(value)
            current["page_end"] = page_number

    def close_current() -> None:
        nonlocal current
        if not current:
            return
        # Physical PDF line/page wraps often split a Korean word between
        # syllables. Concatenate wrapped fragments; spaces already present in
        # the source line are preserved.
        text = normalize("".join(current.pop("_parts")))
        if text:
            current["utterance_text"] = text
            current["content_sha256"] = sha256(text)
            output.append(current)
        current = None

    for page_number, raw_page in enumerate(pages, start=1):
        page = clean_page_for_speeches(raw_page, page_number)
        matches = list(SPEAKER_MARK.finditer(page))
        if not matches:
            append_text(page, page_number)
            continue
        append_text(page[: matches[0].start()], page_number)
        for index, match in enumerate(matches):
            close_current()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(page)
            segment = page[match.end() : end].strip()
            if not segment:
                continue
            lines = segment.splitlines()
            label, name, position, legislator_id, first_content = parse_speaker_line(
                lines[0], aliases, alias_to_id
            )
            sequence_no = len(output) + 1
            current = {
                "utterance_id": f"{meeting.meeting_id}:utterance:{sequence_no}",
                "meeting_id": meeting.meeting_id,
                "sequence_no": sequence_no,
                "speaker_member_id": None,
                "source_speaker_id": None,
                "legislator_id": legislator_id,
                "speaker_label": label,
                "speaker_name": name,
                "speaker_position": position,
                "page_start": page_number,
                "page_end": page_number,
                "source_pdf_gcs_uri": meeting.raw_pdf_gcs_uri,
                "meeting_date": meeting.meeting_date.isoformat(),
                "meeting_type": meeting.meeting_type,
                "committee_name": meeting.committee_name,
                "collected_at": collected_at,
                "parser_version": PARSER_VERSION,
                "agenda_ids": [],
                "agenda_link_method": "unresolved",
                "source_anchor": f"page:{page_number}",
                "block_id": None,
                "_parts": [first_content, unwrap("\n".join(lines[1:]))],
            }
    close_current()
    for index, row in enumerate(output, start=1):
        row["sequence_no"] = index
        row["utterance_id"] = f"{meeting.meeting_id}:utterance:{index}"
    return output


def process_meeting(
    meeting: Any,
    storage_client: storage.Client,
    aliases: list[str],
    alias_to_id: dict[str, str],
    collected_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    uri = meeting.raw_pdf_gcs_uri
    bucket_name, blob_name = uri[5:].split("/", 1)
    pdf = storage_client.bucket(bucket_name).blob(blob_name).download_as_bytes()
    if not pdf.startswith(b"%PDF"):
        raise RuntimeError(f"invalid PDF: {meeting.meeting_id}")
    pages = extract_pages(pdf)
    page_rows = [
        {
            "page_id": f"{meeting.meeting_id}:page:{page_number}",
            "meeting_id": meeting.meeting_id,
            "page_number": page_number,
            "extracted_text": text,
            "source_pdf_gcs_uri": uri,
            "content_sha256": sha256(text),
            "extraction_method": "pdftotext-raw",
            "parser_version": PARSER_VERSION,
            "meeting_date": meeting.meeting_date.isoformat(),
            "meeting_type": meeting.meeting_type,
            "committee_name": meeting.committee_name,
            "collected_at": collected_at,
        }
        for page_number, text in enumerate(pages, start=1)
    ]
    utterances = parse_utterances(meeting, pages, aliases, alias_to_id, collected_at)
    return page_rows, utterances


def write_rows(handle: Any, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        handle.write((json.dumps(row, ensure_ascii=False, default=json_default) + "\n").encode())
        count += 1
    return count


def cached_agendas(
    storage_client: storage.Client,
    bucket_name: str,
    meeting_ids: set[str],
    collected_at: str,
) -> list[dict[str, Any]]:
    discovered: dict[tuple[str, str], dict[str, Any]] = {}
    pattern = re.compile(r"raw/api/(plenary|committee)/year=\d{4}/date=.*?/response[.]json$")
    for blob in storage_client.list_blobs(bucket_name, prefix="raw/api/"):
        match = pattern.fullmatch(blob.name)
        if not match:
            continue
        meeting_type = match.group(1)
        payload = json.loads(blob.download_as_bytes())
        for sections in payload.values():
            if not isinstance(sections, list) or len(sections) < 2:
                continue
            for source in sections[1].get("row", []):
                meeting_id = f"{meeting_type}:{source.get('CONFER_NUM')}"
                title = normalize(source.get("SUB_NAME"))
                if meeting_id not in meeting_ids or not title:
                    continue
                number_match = re.match(r"(\d+)\s*[.]\s*", title)
                bill_match = re.search(r"의안번호\s*(\d+)", title)
                discovered[(meeting_id, title)] = {
                    "meeting_id": meeting_id,
                    "agenda_no": int(number_match.group(1)) if number_match else None,
                    "title": title,
                    "bill_number": bill_match.group(1) if bill_match else None,
                    "bill_id": None,
                    "source_anchor": None,
                    "collected_at": collected_at,
                }
    rows = sorted(
        discovered.values(),
        key=lambda row: (row["meeting_id"], row["agenda_no"] or 10**9, row["title"]),
    )
    counters: dict[str, int] = {}
    for row in rows:
        counters[row["meeting_id"]] = counters.get(row["meeting_id"], 0) + 1
        row["agenda_id"] = f"{row['meeting_id']}:agenda:{counters[row['meeting_id']]}"
    return rows


def schema(table: str) -> list[bigquery.SchemaField]:
    if table == "pdf_pages":
        return [
            bigquery.SchemaField("page_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("meeting_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("page_number", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("extracted_text", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("source_pdf_gcs_uri", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("content_sha256", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("extraction_method", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("parser_version", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("meeting_date", "DATE", mode="REQUIRED"),
            bigquery.SchemaField("meeting_type", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("committee_name", "STRING"),
            bigquery.SchemaField("collected_at", "TIMESTAMP", mode="REQUIRED"),
        ]
    if table == "utterances":
        return [
            bigquery.SchemaField("utterance_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("meeting_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("sequence_no", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("speaker_member_id", "STRING"),
            bigquery.SchemaField("source_speaker_id", "STRING"),
            bigquery.SchemaField("legislator_id", "STRING"),
            bigquery.SchemaField("speaker_label", "STRING"),
            bigquery.SchemaField("speaker_name", "STRING"),
            bigquery.SchemaField("speaker_position", "STRING"),
            bigquery.SchemaField("utterance_text", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("content_sha256", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("page_start", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("page_end", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("source_pdf_gcs_uri", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("agenda_ids", "STRING", mode="REPEATED"),
            bigquery.SchemaField("agenda_link_method", "STRING"),
            bigquery.SchemaField("source_anchor", "STRING"),
            bigquery.SchemaField("meeting_date", "DATE", mode="REQUIRED"),
            bigquery.SchemaField("meeting_type", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("committee_name", "STRING"),
            bigquery.SchemaField("collected_at", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("parser_version", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("block_id", "STRING"),
        ]
    if table == "agendas":
        return [
            bigquery.SchemaField("agenda_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("meeting_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("agenda_no", "INTEGER"),
            bigquery.SchemaField("title", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("bill_number", "STRING"),
            bigquery.SchemaField("bill_id", "STRING"),
            bigquery.SchemaField("source_anchor", "STRING"),
            bigquery.SchemaField("collected_at", "TIMESTAMP", mode="REQUIRED"),
        ]
    raise ValueError(table)


def load_file(client: bigquery.Client, table: str, handle: Any, table_schema: list[bigquery.SchemaField]) -> None:
    client.delete_table(table, not_found_ok=True)
    client.create_table(bigquery.Table(table, schema=table_schema))
    handle.seek(0)
    config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        schema=table_schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    client.load_table_from_file(handle, table, job_config=config).result()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=PROJECT)
    parser.add_argument("--dataset", default=DATASET)
    parser.add_argument("--assembly-no", type=int, default=22)
    parser.add_argument("--meeting-id")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--agendas-only", action="store_true")
    parser.add_argument("--bucket", default="proj-aj04-211200020328-assembly-us")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("ASSEMBLY_API_KEY")
    if not api_key:
        raise SystemExit("set ASSEMBLY_API_KEY")
    api_rows, _ = fetch_members(api_key, args.assembly_no)
    aliases, alias_to_id = member_aliases(api_rows)
    bq = bigquery.Client(project=args.project)
    where = "WHERE meeting_id = @meeting_id" if args.meeting_id else ""
    config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("meeting_id", "STRING", args.meeting_id)]
        if args.meeting_id else []
    )
    meetings = list(
        bq.query(
            f"SELECT * FROM `{args.project}.{args.dataset}.meetings` {where} ORDER BY meeting_id",
            job_config=config,
        ).result()
    )
    collected_at = datetime.now(timezone.utc).isoformat()
    storage_client = storage.Client(project=args.project)
    if args.agendas_only:
        rows = cached_agendas(
            storage_client, args.bucket, {row.meeting_id for row in meetings}, collected_at
        )
        print(f"agendas={len(rows):,}")
        if not args.apply:
            print("dry run; no BigQuery changes")
            return 0
        agenda_file = tempfile.TemporaryFile()
        write_rows(agenda_file, rows)
        staging = f"{args.project}.{args.dataset}.agendas_pdf_staging"
        load_file(bq, staging, agenda_file, schema("agendas"))
        duplicates = next(
            iter(
                bq.query(
                    f"SELECT COUNT(*) n FROM (SELECT agenda_id FROM `{staging}` "
                    "GROUP BY agenda_id HAVING COUNT(*) > 1)"
                ).result()
            )
        ).n
        if duplicates:
            raise RuntimeError(f"duplicate agenda IDs: {duplicates}")
        print(f"staging ready: {staging}")
        return 0
    page_file = tempfile.TemporaryFile()
    utterance_file = tempfile.TemporaryFile()
    page_count = utterance_count = parsed_speakers = mapped_speakers = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(
                process_meeting, meeting, storage_client, aliases, alias_to_id, collected_at
            ): meeting.meeting_id
            for meeting in meetings
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            page_rows, utterance_rows = future.result()
            page_count += write_rows(page_file, page_rows)
            utterance_count += write_rows(utterance_file, utterance_rows)
            parsed_speakers += sum(row["speaker_name"] is not None for row in utterance_rows)
            mapped_speakers += sum(row["legislator_id"] is not None for row in utterance_rows)
            if completed % 25 == 0 or completed == len(futures):
                print(f"parsed {completed}/{len(futures)}", flush=True)

    print(f"pages={page_count:,}")
    print(f"utterances={utterance_count:,}")
    print(f"speaker_parse_rate={parsed_speakers / max(utterance_count, 1):.4%}")
    print(f"legislator_mapped_utterances={mapped_speakers:,}")
    if not args.apply:
        print("dry run; no BigQuery changes")
        return 0

    prefix = f"{args.project}.{args.dataset}"
    page_staging = f"{prefix}.pdf_pages_staging"
    utterance_staging = f"{prefix}.utterances_pdf_staging"
    load_file(bq, page_staging, page_file, schema("pdf_pages"))
    load_file(bq, utterance_staging, utterance_file, schema("utterances"))
    metrics = {
        row.metric: int(row.value)
        for row in bq.query(
            f"""
            SELECT 'duplicate_pages' metric, COUNT(*) value FROM (
              SELECT page_id FROM `{page_staging}` GROUP BY page_id HAVING COUNT(*) > 1)
            UNION ALL SELECT 'duplicate_utterances', COUNT(*) FROM (
              SELECT utterance_id FROM `{utterance_staging}` GROUP BY utterance_id HAVING COUNT(*) > 1)
            UNION ALL SELECT 'orphan_pages', COUNT(*) FROM `{page_staging}` p
              LEFT JOIN `{prefix}.meetings` m USING(meeting_id) WHERE m.meeting_id IS NULL
            UNION ALL SELECT 'orphan_utterances', COUNT(*) FROM `{utterance_staging}` u
              LEFT JOIN `{prefix}.meetings` m USING(meeting_id) WHERE m.meeting_id IS NULL
            UNION ALL SELECT 'invalid_page_ranges', COUNT(*) FROM `{utterance_staging}`
              WHERE page_start < 1 OR page_end < page_start
            UNION ALL SELECT 'empty_utterances', COUNT(*) FROM `{utterance_staging}`
              WHERE TRIM(utterance_text) = ''
            """
        ).result()
    }
    print(json.dumps(metrics, indent=2))
    if any(metrics.values()):
        raise RuntimeError("staging validation failed")
    print(f"staging ready: {page_staging}, {utterance_staging}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
