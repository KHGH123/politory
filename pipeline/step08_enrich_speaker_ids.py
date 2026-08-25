#!/usr/bin/env python3
"""Enrich PDF-derived utterances with verified official web speaker IDs.

The PDF remains the canonical text source. The official web viewer is used only
as identity evidence after the meeting ID/date/assembly and text are validated.
Dry-run is the default; ``--apply`` is required for BigQuery/GCS writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from google.cloud import bigquery, storage

from incremental_pipeline.documents import build_search_documents, delete_tables
from step01_collect_assembly import MinutesParser
from step03_normalize_legislators import is_legislative_role


PROJECT = "proj-aj04-211200020328"
DATASET = "assembly"
BUCKET = "proj-aj04-211200020328-assembly-us"
USER_AGENT = "Mozilla/5.0 (compatible; AssemblySpeakerIdentityEnricher/1.0)"
PARSER_VERSION = "speaker-web-identity-v1"
LEGISLATIVE_POSITIONS = re.compile(r"(?:위원장|위원장대리|소위원장|부위원장|위원|의원|간사|국회의장|의장|부의장)")


@dataclass(frozen=True)
class Meeting:
    meeting_id: str
    confer_num: int
    assembly_no: int
    meeting_date: date
    meeting_type: str
    committee_name: str | None
    title: str
    official_url: str


def normalize(value: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip()


def compact(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"[^0-9A-Za-z가-힣一-龥]", "", value).lower()


def image_key(url: str | None) -> str | None:
    if not url:
        return None
    name = PurePosixPath(urlparse(url).path).name
    return name.lower() or None


def name_keys(value: str | None) -> set[str]:
    """Return official viewer name variants such as 朴芝源(박지원)."""
    value = normalize(value)
    if not value:
        return set()
    keys = {value}
    match = re.fullmatch(r"([^()]+)\(([^()]+)\)", value)
    if match:
        keys.update(normalize(part) for part in match.groups())
    return keys - {""}


def view_url(url: str, confer_num: int) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["id"] = [str(confer_num)]
    query["type"] = ["view"]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def summary_url(url: str, confer_num: int) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["id"] = [str(confer_num)]
    query["type"] = ["summary"]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def fetch_html(url: str, timeout: int = 30) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_web(html: bytes) -> tuple[str, list[dict[str, Any]]]:
    text = html.decode("utf-8", "replace")
    parser = MinutesParser()
    parser.feed(text)
    speeches = [block for block in parser.blocks() if block["block_type"] == "speech"]
    return text, speeches


def parse_attendees(html: bytes) -> tuple[str, list[dict[str, Any]]]:
    """Extract official member IDs and roster labels from the summary page."""
    text = html.decode("utf-8", "replace")
    attendees: list[dict[str, Any]] = []
    inputs = re.compile(
        r"<input\b[^>]*class=[\"'][^\"']*\bspeakerMem\b[^\"']*[\"'][^>]*>",
        re.I,
    )
    attrs_pattern = re.compile(r"([\w:-]+)\s*=\s*[\"']([^\"']*)[\"']")
    for match in inputs.finditer(text):
        attrs = {key.lower(): value for key, value in attrs_pattern.findall(match.group(0))}
        source_id = normalize(attrs.get("data-mem_id"))
        name = normalize(attrs.get("data-name"))
        if not source_id or source_id == "0" or not name:
            continue
        label_html = text[match.end() : match.end() + 1200].split("</label>", 1)[0]
        area_match = re.search(
            r"<span\b[^>]*class=[\"'][^\"']*\barea\b[^\"']*[\"'][^>]*>(.*?)</span>",
            label_html,
            re.I | re.S,
        )
        area = None
        if area_match:
            area = normalize(re.sub(r"<[^>]+>", "", area_match.group(1))).strip("() ") or None
        attendees.append(
            {
                "member_id": source_id,
                "name": name,
                "position": normalize(attrs.get("data-pos")) or None,
                "district_label": area,
                "profile_url": None,
                "profile_image_url": None,
            }
        )
    return text, attendees


def leading_anchor(value: str, minimum: int = 30, maximum: int = 100) -> str:
    """Use the first sentence, extending through later sentences when too short."""
    parts = re.split(r"(?<=[.!?。？！])\s*|\n+", normalize(value))
    anchor = ""
    for part in parts:
        anchor += compact(part)
        if len(anchor) >= minimum:
            break
    return anchor[:maximum]


def best_text_similarity(value: str, candidates: list[str]) -> float:
    """Verify the normalized first sentence against same-speaker web blocks."""
    anchor = leading_anchor(value)
    if not anchor:
        return 0.0
    return 1.0 if any(anchor in compact(candidate) for candidate in candidates) else 0.0


def corpus_anchor_match(value: str, corpus: str) -> bool:
    anchor = leading_anchor(value)
    if len(anchor) < 30:
        return False
    return anchor in corpus


def validate_meeting(
    meeting: Meeting, html_text: str, attendees: list[dict[str, Any]]
) -> dict[str, Any]:
    id_match = re.search(r"const\s+mnts_id\s*=\s*(\d+)\s*;", html_text)
    id_ok = bool(id_match and int(id_match.group(1)) == meeting.confer_num)
    date_tokens = {
        meeting.meeting_date.strftime("%Y.%m.%d"),
        f"{meeting.meeting_date.year}년 {meeting.meeting_date.month}월 {meeting.meeting_date.day}일",
    }
    date_ok = any(token in html_text for token in date_tokens)
    assembly_ok = f"제{meeting.assembly_no}대" in html_text

    committee_ok = not meeting.committee_name or compact(meeting.committee_name) in compact(html_text)
    valid = id_ok and date_ok and assembly_ok and committee_ok and bool(attendees)
    return {
        "valid": valid,
        "id_ok": id_ok,
        "date_ok": date_ok,
        "assembly_ok": assembly_ok,
        "committee_ok": committee_ok,
        "attendee_count": len(attendees),
    }


def fetch_validated_attendees(
    meeting: Meeting, attempts: int = 5, base_backoff: float = 2.0
) -> tuple[str, bytes, list[dict[str, Any]], dict[str, Any]]:
    """Retry the stateful viewer and return only a meeting-verified response."""
    url = summary_url(meeting.official_url, meeting.confer_num)
    last_error: str | None = None
    last_validation: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        try:
            html = fetch_html(url)
            html_text, attendees = parse_attendees(html)
            validation = validate_meeting(meeting, html_text, attendees)
            last_validation = validation
            if validation["valid"]:
                return url, html, attendees, validation
            last_error = f"validation failed: {validation}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < attempts:
            # The official viewer intermittently returns another meeting or
            # HTTP 400/502 while overloaded. Back off before trying the exact
            # same, fully validated meeting URL again.
            delay = base_backoff * (2 ** (attempt - 1))
            time.sleep(delay + random.uniform(0, min(1.0, delay * 0.1)))
    raise RuntimeError(
        f"official summary failed after {attempts} attempts: "
        f"{last_error}; last_validation={last_validation}"
    )


def load_member_index(client: bigquery.Client, project: str, dataset: str, assembly_no: int) -> dict[str, Any]:
    rows = list(
        client.query(
            f"""
            SELECT m.id AS mps_id, m.name, m.image_url, m.district,
                   m.assembly_member_code, l.legislator_id
            FROM `{project}.{dataset}.mps` AS m
            JOIN `{project}.{dataset}.legislators` AS l
              ON m.assembly_member_code = l.official_member_code
             AND l.assembly_no = @assembly_no
            """,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[bigquery.ScalarQueryParameter("assembly_no", "INT64", assembly_no)]
            ),
        ).result()
    )
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_image: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_district: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_code: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row.items())
        by_name[normalize(item["name"])].append(item)
        if item.get("district"):
            by_district[compact(item["district"])].append(item)
        by_code[normalize(item["assembly_member_code"]).upper()] = item
        key = image_key(item.get("image_url"))
        if key:
            by_image[key].append(item)
    return {
        "by_name": by_name,
        "by_image": by_image,
        "by_code": by_code,
        "by_district": by_district,
    }


def resolve_member(blocks: list[dict[str, Any]], member_index: dict[str, Any]) -> tuple[str | None, str, float]:
    image_keys = {image_key(block.get("profile_image_url")) for block in blocks} - {None}
    code_candidates = {
        key.rsplit(".", 1)[0].upper()
        for key in image_keys
        if key and "." in key
    }
    code_matches = {
        member_index["by_code"][code]["legislator_id"]
        for code in code_candidates
        if code in member_index["by_code"]
    }
    if len(code_matches) == 1:
        return next(iter(code_matches)), "OFFICIAL_CODE_IN_PROFILE_IMAGE", 1.0
    image_candidates: dict[str, dict[str, Any]] = {}
    for key in image_keys:
        for item in member_index["by_image"].get(key, []):
            image_candidates[item["legislator_id"]] = item
    if len(image_candidates) == 1:
        return next(iter(image_candidates)), "OFFICIAL_PROFILE_IMAGE", 1.0
    names = {name for block in blocks for name in name_keys(block.get("name"))}
    name_candidates: dict[str, dict[str, Any]] = {}
    for name in names:
        for item in member_index["by_name"].get(name, []):
            name_candidates[item["legislator_id"]] = item
    if len(name_candidates) == 1:
        return next(iter(name_candidates)), "UNIQUE_OFFICIAL_NAME", 0.99
    districts = {compact(block.get("district_label")) for block in blocks} - {""}
    district_candidates: dict[str, dict[str, Any]] = {}
    for district in districts:
        for item in member_index["by_district"].get(district, []):
            district_candidates[item["legislator_id"]] = item
    shared = set(name_candidates) & set(district_candidates)
    if len(shared) == 1:
        return next(iter(shared)), "OFFICIAL_ATTENDEE_NAME_AND_DISTRICT", 0.995
    return None, "AMBIGUOUS_MEMBER_PROFILE", 0.0


def evidence_for_meeting(
    meeting: Meeting,
    utterances: list[dict[str, Any]],
    attendees: list[dict[str, Any]],
    member_index: dict[str, Any],
    source_url: str,
    source_html_gcs_uri: str | None,
    source_sha256: str,
    speaker_names: set[str],
    collected_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    web_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for block in attendees:
        source_id = normalize(block.get("member_id"))
        if source_id and source_id != "0" and is_legislative_role(block.get("position")):
            for key in name_keys(block.get("name")):
                web_by_name[key].append(block)

    pdf_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in utterances:
        pdf_by_name[normalize(row.get("speaker_name"))].append(row)

    links: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for name, pdf_rows in pdf_by_name.items():
        if speaker_names and name not in speaker_names:
            continue
        web_rows = web_by_name.get(name, [])
        source_ids = {normalize(row.get("member_id")) for row in web_rows}
        if len(source_ids) != 1:
            continue
        source_id = next(iter(source_ids))
        legislator_id, resolution_method, confidence = resolve_member(web_rows, member_index)
        if not legislator_id:
            continue

        profile_urls = sorted({row.get("profile_url") for row in web_rows if row.get("profile_url")})
        profile_images = sorted({row.get("profile_image_url") for row in web_rows if row.get("profile_image_url")})
        links.append(
            {
                "assembly_no": meeting.assembly_no,
                "source_speaker_id": source_id,
                "speaker_name": name,
                "legislator_id": legislator_id,
                "profile_url": profile_urls[0] if len(profile_urls) == 1 else None,
                "profile_image_url": profile_images[0] if len(profile_images) == 1 else None,
                "resolution_method": resolution_method,
                "confidence": confidence,
                "verified_at": collected_at,
            }
        )
        for pdf in pdf_rows:
            if not is_legislative_role(pdf.get("speaker_position")):
                continue
            evidence_id = hashlib.sha256(
                f"{meeting.meeting_id}|{name}|{pdf.get('speaker_position')}|"
                f"{source_id}|{legislator_id}".encode()
            ).hexdigest()
            evidence.append(
                {
                    "evidence_id": evidence_id,
                    "meeting_id": meeting.meeting_id,
                    "assembly_no": meeting.assembly_no,
                    "speaker_name": name,
                    "speaker_position": pdf.get("speaker_position"),
                    "utterance_count": int(pdf["utterance_count"]),
                    "source_speaker_id": source_id,
                    "legislator_id": legislator_id,
                    "resolution_method": resolution_method,
                    "confidence": confidence,
                    "text_similarity": None,
                    "source_url": source_url,
                    "source_html_gcs_uri": source_html_gcs_uri,
                    "source_sha256": source_sha256,
                    "parser_version": PARSER_VERSION,
                    "validation_status": "VERIFIED",
                    "collected_at": collected_at,
                }
            )
    return links, evidence


def ensure_tables(client: bigquery.Client, project: str, dataset: str) -> None:
    prefix = f"{project}.{dataset}"
    client.query(
        f"""
        CREATE TABLE IF NOT EXISTS `{prefix}.source_speaker_members` (
          assembly_no INT64 NOT NULL,
          source_speaker_id STRING NOT NULL,
          speaker_name STRING,
          legislator_id STRING NOT NULL,
          profile_url STRING,
          profile_image_url STRING,
          resolution_method STRING NOT NULL,
          confidence FLOAT64,
          verified_at TIMESTAMP NOT NULL
        )
        CLUSTER BY assembly_no, source_speaker_id, legislator_id;

        CREATE TABLE IF NOT EXISTS `{prefix}.speaker_identity_evidence` (
          evidence_id STRING NOT NULL,
          meeting_id STRING NOT NULL,
          assembly_no INT64 NOT NULL,
          speaker_name STRING,
          speaker_position STRING,
          utterance_count INT64 NOT NULL,
          source_speaker_id STRING NOT NULL,
          legislator_id STRING NOT NULL,
          resolution_method STRING NOT NULL,
          confidence FLOAT64,
          text_similarity FLOAT64,
          source_url STRING NOT NULL,
          source_html_gcs_uri STRING,
          source_sha256 STRING NOT NULL,
          parser_version STRING NOT NULL,
          validation_status STRING NOT NULL,
          collected_at TIMESTAMP NOT NULL
        )
        CLUSTER BY meeting_id, source_speaker_id, legislator_id;
        """
    ).result()


def load_json_rows(client: bigquery.Client, table_id: str, rows: list[dict[str, Any]]) -> None:
    errors = client.insert_rows_json(table_id, rows)
    if errors:
        raise RuntimeError(f"failed to load {table_id}: {errors[:3]}")


def apply_evidence(
    client: bigquery.Client,
    project: str,
    dataset: str,
    links: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> None:
    if not evidence:
        return
    prefix = f"{project}.{dataset}"
    suffix = hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:12]
    links_table = f"{prefix}._source_speaker_members_{suffix}"
    evidence_table = f"{prefix}._speaker_identity_evidence_{suffix}"
    try:
        client.create_table(bigquery.Table(links_table, schema=client.get_table(f"{prefix}.source_speaker_members").schema))
        client.create_table(bigquery.Table(evidence_table, schema=client.get_table(f"{prefix}.speaker_identity_evidence").schema))
        load_json_rows(client, links_table, links)
        load_json_rows(client, evidence_table, evidence)
        sql = f"""
        BEGIN TRANSACTION;

        ASSERT NOT EXISTS (
          SELECT 1
          FROM `{prefix}.source_speaker_members` T
          JOIN `{links_table}` S
            ON T.assembly_no=S.assembly_no
           AND T.source_speaker_id=S.source_speaker_id
          WHERE T.legislator_id != S.legislator_id
        ) AS 'source speaker ID conflicts with an existing legislator mapping';

        MERGE `{prefix}.source_speaker_members` T
        USING `{links_table}` S
        ON T.assembly_no=S.assembly_no AND T.source_speaker_id=S.source_speaker_id
        WHEN NOT MATCHED THEN INSERT ROW;

        MERGE `{prefix}.speaker_identity_evidence` T
        USING `{evidence_table}` S
        ON T.evidence_id=S.evidence_id
        WHEN MATCHED THEN UPDATE SET validation_status=S.validation_status,
          text_similarity=S.text_similarity, source_html_gcs_uri=S.source_html_gcs_uri,
          source_sha256=S.source_sha256, collected_at=S.collected_at
        WHEN NOT MATCHED THEN INSERT ROW;

        UPDATE `{prefix}.utterances` U
        SET source_speaker_id=E.source_speaker_id, legislator_id=E.legislator_id
        FROM `{evidence_table}` E
        WHERE U.meeting_id=E.meeting_id
          AND NORMALIZE(COALESCE(U.speaker_name, ''), NFKC)=NORMALIZE(COALESCE(E.speaker_name, ''), NFKC)
          AND NORMALIZE(COALESCE(U.speaker_position, ''), NFKC)=NORMALIZE(COALESCE(E.speaker_position, ''), NFKC)
          AND (U.source_speaker_id IS NULL OR U.source_speaker_id=E.source_speaker_id)
          AND (U.legislator_id IS NULL OR U.legislator_id=E.legislator_id);

        COMMIT TRANSACTION;
        """
        client.query(sql).result()
    finally:
        delete_tables(client, [links_table, evidence_table])


def backfill_verified_links(
    client: bigquery.Client,
    project: str,
    dataset: str,
    assembly_no: int,
    update_search_documents: bool = True,
    meeting_ids: list[str] | None = None,
) -> None:
    """Propagate verified one-to-one IDs globally or within selected meetings."""
    prefix = f"{project}.{dataset}"
    meeting_ids = sorted(set(meeting_ids or []))
    meeting_scope = "AND M.meeting_id IN UNNEST(@meeting_ids)" if meeting_ids else ""
    document_scope = (
        "AND JSON_VALUE(D.jsonData, '$.meeting_id') IN UNNEST(@meeting_ids)"
        if meeting_ids
        else ""
    )
    search_sql = ""
    if update_search_documents:
        search_sql = f"""
        UPDATE `{prefix}.search_documents` D
        SET jsonData=TO_JSON_STRING(JSON_SET(
          PARSE_JSON(D.jsonData),
          '$.source_speaker_id', L.source_speaker_id,
          '$.identity_status', 'MATCHED'
        ))
        FROM links L
        WHERE JSON_VALUE(D.jsonData, '$.legislator_id')=L.legislator_id
          AND SAFE_CAST(JSON_VALUE(D.jsonData, '$.assembly_no') AS INT64)=@assembly_no
          {document_scope}
          AND JSON_VALUE(D.jsonData, '$.source_speaker_id') IS NULL;

        UPDATE `{prefix}.search_documents` D
        SET jsonData=TO_JSON_STRING(JSON_SET(
          PARSE_JSON(D.jsonData),
          '$.source_speaker_id', N.source_speaker_id,
          '$.legislator_id', N.legislator_id,
          '$.identity_status', 'MATCHED'
        ))
        FROM name_links N
        WHERE JSON_VALUE(D.jsonData, '$.legislator_id') IS NULL
          AND SAFE_CAST(JSON_VALUE(D.jsonData, '$.assembly_no') AS INT64)=@assembly_no
          {document_scope}
          AND NORMALIZE(JSON_VALUE(D.jsonData, '$.speaker_name'), NFKC)=N.speaker_name
          AND (
            JSON_VALUE(D.jsonData, '$.speaker_position') IN
              ('위원','의원','의장','부의장','간사','위원장','소위원장','부위원장')
            OR ENDS_WITH(JSON_VALUE(D.jsonData, '$.speaker_position'), '위원장')
          )
          AND NOT CONTAINS_SUBSTR(
            COALESCE(JSON_VALUE(D.jsonData, '$.speaker_position'), ''), '국무위원'
          );

        UPDATE `{prefix}.search_documents` D
        SET jsonData=TO_JSON_STRING(JSON_SET(
          PARSE_JSON(D.jsonData),
          '$.source_speaker_id', U.source_speaker_id,
          '$.legislator_id', U.legislator_id,
          '$.identity_status', 'MATCHED'
        ))
        FROM `{prefix}.utterances` U
        JOIN `{prefix}.meetings` M
          ON U.meeting_id=M.meeting_id AND M.assembly_no=@assembly_no
        WHERE JSON_VALUE(D.jsonData, '$.primary_utterance_id')=U.utterance_id
          {meeting_scope}
          AND U.source_speaker_id IS NOT NULL
          AND U.legislator_id IS NOT NULL
          AND (
            JSON_VALUE(D.jsonData, '$.source_speaker_id') IS NULL
            OR JSON_VALUE(D.jsonData, '$.legislator_id') IS NULL
          );
        """
    client.query(
        f"""
        BEGIN TRANSACTION;

        CREATE TEMP TABLE link_pairs AS
        SELECT DISTINCT legislator_id, source_speaker_id
        FROM `{prefix}.source_speaker_members`
        WHERE assembly_no=@assembly_no;

        CREATE TEMP TABLE unique_people AS
        SELECT legislator_id
        FROM link_pairs
        GROUP BY legislator_id
        HAVING COUNT(*)=1;

        CREATE TEMP TABLE links AS
        SELECT P.*
        FROM link_pairs P
        JOIN unique_people USING (legislator_id);

        CREATE TEMP TABLE name_pairs AS
        SELECT DISTINCT NORMALIZE(speaker_name, NFKC) speaker_name,
               legislator_id, source_speaker_id
        FROM `{prefix}.source_speaker_members`
        WHERE assembly_no=@assembly_no
          AND speaker_name IS NOT NULL;

        CREATE TEMP TABLE unique_names AS
        SELECT speaker_name
        FROM name_pairs
        GROUP BY speaker_name
        HAVING COUNT(*)=1;

        CREATE TEMP TABLE name_links AS
        SELECT P.*
        FROM name_pairs P
        JOIN unique_names USING (speaker_name);

        UPDATE `{prefix}.utterances` U
        SET source_speaker_id=L.source_speaker_id
        FROM links L
        JOIN `{prefix}.meetings` M ON M.assembly_no=@assembly_no
        WHERE U.legislator_id=L.legislator_id
          AND U.meeting_id=M.meeting_id
          {meeting_scope}
          AND U.source_speaker_id IS NULL;

        UPDATE `{prefix}.utterances` U
        SET source_speaker_id=N.source_speaker_id, legislator_id=N.legislator_id
        FROM name_links N
        JOIN `{prefix}.meetings` M ON M.assembly_no=@assembly_no
        WHERE U.legislator_id IS NULL
          AND U.meeting_id=M.meeting_id
          {meeting_scope}
          AND NORMALIZE(U.speaker_name, NFKC)=N.speaker_name
          AND (
            U.speaker_position IN
              ('위원','의원','의장','부의장','간사','위원장','소위원장','부위원장')
            OR ENDS_WITH(U.speaker_position, '위원장')
          )
          AND NOT CONTAINS_SUBSTR(COALESCE(U.speaker_position, ''), '국무위원');

        {search_sql}
        COMMIT TRANSACTION;
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("assembly_no", "INT64", assembly_no),
            *(
                [bigquery.ArrayQueryParameter("meeting_ids", "STRING", meeting_ids)]
                if meeting_ids
                else []
            ),
        ]),
    ).result()


def query_scope(
    client: bigquery.Client,
    project: str,
    dataset: str,
    assembly_no: int,
    speaker_names: list[str],
    meeting_ids: list[str],
    limit: int | None,
) -> tuple[list[Meeting], dict[str, list[dict[str, Any]]]]:
    predicates = [
        "m.assembly_no=@assembly_no",
        "m.official_url IS NOT NULL",
        "(u.source_speaker_id IS NULL OR u.legislator_id IS NULL)",
        "REGEXP_CONTAINS(COALESCE(u.speaker_position, ''), r'(위원|의원|의장|간사)')",
        f"(NORMALIZE(u.speaker_name, NFKC) IN ("
        f"SELECT DISTINCT NORMALIZE(name, NFKC) FROM `{project}.{dataset}.mps`"
        f") OR REGEXP_CONTAINS(COALESCE(u.speaker_name, ''), r'[一-龥]'))",
    ]
    parameters: list[bigquery.QueryParameter] = [bigquery.ScalarQueryParameter("assembly_no", "INT64", assembly_no)]
    if speaker_names:
        predicates.append("NORMALIZE(u.speaker_name, NFKC) IN UNNEST(@speaker_names)")
        parameters.append(bigquery.ArrayQueryParameter("speaker_names", "STRING", speaker_names))
    if meeting_ids:
        predicates.append("m.meeting_id IN UNNEST(@meeting_ids)")
        parameters.append(bigquery.ArrayQueryParameter("meeting_ids", "STRING", meeting_ids))
    limit_sql = f"LIMIT {limit}" if limit else ""
    config = bigquery.QueryJobConfig(query_parameters=parameters)
    meetings = [
        Meeting(**dict(row.items()))
        for row in client.query(
            f"""
            SELECT DISTINCT m.meeting_id, m.confer_num, m.assembly_no, m.meeting_date,
                   m.meeting_type, m.committee_name, m.title, m.official_url
            FROM `{project}.{dataset}.meetings` m
            JOIN `{project}.{dataset}.utterances` u USING (meeting_id)
            WHERE {' AND '.join(predicates)}
            ORDER BY m.meeting_date, m.meeting_id
            {limit_sql}
            """,
            job_config=config,
        ).result()
    ]
    if not meetings:
        return [], {}
    ids = [meeting.meeting_id for meeting in meetings]
    rows_by_meeting: dict[str, list[dict[str, Any]]] = defaultdict(list)
    row_parameters: list[bigquery.QueryParameter] = [
        bigquery.ArrayQueryParameter("meeting_ids", "STRING", ids)
    ]
    target_filter = ""
    if speaker_names:
        target_filter = "AND NORMALIZE(speaker_name, NFKC) IN UNNEST(@speaker_names)"
        row_parameters.append(
            bigquery.ArrayQueryParameter("speaker_names", "STRING", speaker_names)
        )
    for row in client.query(
        f"""
        SELECT meeting_id, speaker_name, speaker_position, COUNT(*) AS utterance_count
        FROM `{project}.{dataset}.utterances`
        WHERE meeting_id IN UNNEST(@meeting_ids)
          AND (source_speaker_id IS NULL OR legislator_id IS NULL)
          AND REGEXP_CONTAINS(COALESCE(speaker_position, ''), r'(위원|의원|의장|간사)')
          AND (
            NORMALIZE(speaker_name, NFKC) IN (
              SELECT DISTINCT NORMALIZE(name, NFKC) FROM `{project}.{dataset}.mps`
            )
            OR REGEXP_CONTAINS(COALESCE(speaker_name, ''), r'[一-龥]')
          )
          {target_filter}
        GROUP BY meeting_id, speaker_name, speaker_position
        ORDER BY meeting_id, speaker_name, speaker_position
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=row_parameters),
    ).result(page_size=5000):
        rows_by_meeting[row.meeting_id].append(dict(row.items()))
    return meetings, rows_by_meeting


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verified web speaker-ID enrichment")
    parser.add_argument("--project", default=PROJECT)
    parser.add_argument("--dataset", default=DATASET)
    parser.add_argument("--bucket", default=BUCKET)
    parser.add_argument("--assembly-no", type=int, default=22)
    parser.add_argument("--speaker-name", action="append", default=[])
    parser.add_argument("--meeting-id", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--request-delay", type=float, default=1.5)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--fetch-attempts", type=int, default=5)
    parser.add_argument("--max-consecutive-source-failures", type=int, default=3)
    parser.add_argument("--fail-on-rejected", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--skip-search-documents", action="store_true")
    parser.add_argument(
        "--propagate-verified-links",
        action="store_true",
        help=(
            "Bulk-fill all 22nd-assembly utterances/search documents from verified "
            "one-to-one person links. Required explicitly because its scope is global."
        ),
    )
    parser.add_argument(
        "--verified-links-only",
        action="store_true",
        help="Do not call the official viewer; only propagate already verified mappings.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    names = {normalize(name) for name in args.speaker_name}
    if args.batch_size < 1 or args.workers < 1:
        raise SystemExit("--batch-size and --workers must be at least 1")
    if args.workers != 1:
        raise SystemExit(
            "the stateful official viewer is restricted to --workers 1; "
            "parallel requests previously returned wrong meetings and HTTP 400"
        )
    if args.request_delay < 0 or args.fetch_attempts < 1:
        raise SystemExit("--request-delay must be non-negative and --fetch-attempts at least 1")
    if args.max_consecutive_source_failures < 1:
        raise SystemExit("--max-consecutive-source-failures must be at least 1")
    if args.verified_links_only and not (args.apply and args.propagate_verified_links):
        raise SystemExit(
            "--verified-links-only requires --apply --propagate-verified-links"
        )
    client = bigquery.Client(project=args.project)
    storage_client = storage.Client(project=args.project) if args.apply else None
    if args.apply:
        ensure_tables(client, args.project, args.dataset)
        if args.propagate_verified_links:
            print("Propagating previously verified one-to-one speaker links...")
            backfill_verified_links(
                client,
                args.project,
                args.dataset,
                args.assembly_no,
                update_search_documents=not args.skip_search_documents,
            )
            if args.verified_links_only:
                print("Verified speaker links propagated; no web requests were made.")
                return 0
        elif args.meeting_id:
            print("Applying verified links within requested meetings...", flush=True)
            backfill_verified_links(
                client,
                args.project,
                args.dataset,
                args.assembly_no,
                update_search_documents=False,
                meeting_ids=args.meeting_id,
            )
    meetings, utterances_by_meeting = query_scope(
        client, args.project, args.dataset, args.assembly_no,
        sorted(names), args.meeting_id, args.limit,
    )
    member_index = load_member_index(client, args.project, args.dataset, args.assembly_no)
    known_links: dict[tuple[int, str], dict[str, Any]] = {}
    batch_links: dict[tuple[int, str], dict[str, Any]] = {}
    batch_evidence: list[dict[str, Any]] = []
    batch_meetings: set[str] = set()
    rejected: list[dict[str, Any]] = []
    consecutive_source_failures = 0
    circuit_open = False
    accepted_count = 0
    evidence_group_count = 0
    utterance_count = 0
    rebuilt_document_count = 0
    collected_at = datetime.now(timezone.utc).isoformat()

    def flush_batch() -> None:
        nonlocal rebuilt_document_count
        if not args.apply or not batch_evidence:
            return
        apply_evidence(
            client,
            args.project,
            args.dataset,
            list(batch_links.values()),
            batch_evidence,
        )
        if not args.propagate_verified_links and batch_meetings and not args.skip_search_documents:
            suffix = hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:12]
            delta_table = f"{args.project}.{args.dataset}._search_identity_delta_{suffix}"
            try:
                rebuilt_document_count += build_search_documents(
                    client,
                    args.project,
                    args.dataset,
                    sorted(batch_meetings),
                    delta_table,
                )
            finally:
                delete_tables(client, [delta_table])
        print(
            f"BATCH_APPLIED meetings={len(batch_meetings)} "
            f"evidence_groups={len(batch_evidence)}"
        )
        batch_links.clear()
        batch_evidence.clear()
        batch_meetings.clear()

    print(f"mode={'APPLY' if args.apply else 'DRY_RUN'} meetings={len(meetings)} names={sorted(names) or 'ALL'}")
    # Deliberately sequential: the official viewer is stateful and returned
    # stale/wrong meetings under parallel load. Meeting validation remains the
    # final guard, while pacing and the circuit breaker protect the source.
    for processed, meeting in enumerate(meetings, start=1):
        if processed > 1 and args.request_delay:
            time.sleep(args.request_delay)
        try:
            url, html, attendees, validation = fetch_validated_attendees(
                meeting, attempts=args.fetch_attempts
            )
            consecutive_source_failures = 0
            html_sha256 = hashlib.sha256(html).hexdigest()
            links, evidence = evidence_for_meeting(
                meeting, utterances_by_meeting[meeting.meeting_id], attendees,
                member_index, url, None, html_sha256, names, collected_at,
            )
            if evidence:
                if storage_client:
                    object_name = (
                        f"raw/minutes_identity/{meeting.meeting_type}/year={meeting.meeting_date.year}/"
                        f"confer_num={meeting.confer_num}/summary-{html_sha256[:16]}.html"
                    )
                    storage_client.bucket(args.bucket).blob(object_name).upload_from_string(
                        html, content_type="text/html; charset=utf-8"
                    )
                    gcs_uri = f"gs://{args.bucket}/{object_name}"
                    for row in evidence:
                        row["source_html_gcs_uri"] = gcs_uri
                accepted_count += 1
                evidence_group_count += len(evidence)
                utterance_count += sum(row["utterance_count"] for row in evidence)
                batch_meetings.add(meeting.meeting_id)
                batch_evidence.extend(evidence)
                for link in links:
                    key = (link["assembly_no"], link["source_speaker_id"])
                    previous = known_links.get(key)
                    if previous and previous["legislator_id"] != link["legislator_id"]:
                        raise RuntimeError(f"source speaker collision: {key}: {previous} != {link}")
                    known_links[key] = link
                    batch_links[key] = link
            print(
                f"[{processed}/{len(meetings)}] OK {meeting.meeting_id} "
                f"attendees={len(attendees)} unresolved={len(utterances_by_meeting[meeting.meeting_id])} "
                f"matched_utterances={sum(row['utterance_count'] for row in evidence)}"
            )
        except Exception as exc:
            consecutive_source_failures += 1
            rejected.append({"meeting_id": meeting.meeting_id, "error": str(exc)})
            print(f"[{processed}/{len(meetings)}] ERROR {meeting.meeting_id}: {exc}")
            if consecutive_source_failures >= args.max_consecutive_source_failures:
                circuit_open = True
                print(
                    "SOURCE_CIRCUIT_OPEN "
                    f"consecutive_failures={consecutive_source_failures}; "
                    "stopping before more official-viewer requests",
                    flush=True,
                )
                break
        if args.apply and processed % args.batch_size == 0:
            flush_batch()

    flush_batch()

    if args.apply and args.propagate_verified_links:
        print("Final propagation of all newly verified speaker links...", flush=True)
        backfill_verified_links(
            client,
            args.project,
            args.dataset,
            args.assembly_no,
            update_search_documents=not args.skip_search_documents,
        )

    print(json.dumps({
        "meetings_requested": len(meetings),
        "meetings_accepted": accepted_count,
        "meetings_rejected": len(rejected),
        "source_member_links": len(known_links),
        "evidence_groups": evidence_group_count,
        "matched_utterances": utterance_count,
        "source_circuit_open": circuit_open,
        "search_documents_rebuilt": rebuilt_document_count,
        "rejected": rejected[:20],
    }, ensure_ascii=False, indent=2))
    return 2 if args.fail_on_rejected and rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
