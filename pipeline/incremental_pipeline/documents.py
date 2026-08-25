"""Build and publish meeting-scoped Vertex AI Search document deltas."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from google.cloud import bigquery

from step04_build_search_documents import generate_documents, source_query
from step06_build_vote_search_documents import (
    compact,
    documents_for_vote,
    member_pattern,
    parse_meeting,
)


SQL_DIR = Path(__file__).resolve().parent / "sql"
DOCUMENT_SCHEMA = [
    bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("jsonData", "STRING", mode="NULLABLE"),
]


def _validate_documents(documents: list[dict[str, str]], meeting_ids: set[str]) -> None:
    """Reject duplicate IDs, malformed JSON, or rows outside the requested scope."""
    ids = [document["id"] for document in documents]
    duplicate_ids = [key for key, count in Counter(ids).items() if count > 1]
    if duplicate_ids:
        raise RuntimeError(f"duplicate document IDs: {duplicate_ids[:5]}")
    for document in documents:
        payload = json.loads(document["jsonData"])
        if payload.get("meeting_id") not in meeting_ids:
            raise RuntimeError(
                f"document {document['id']} escaped meeting scope: "
                f"{payload.get('meeting_id')}"
            )


def _load_delta(
    client: bigquery.Client,
    table_id: str,
    documents: list[dict[str, str]],
) -> None:
    """Create one execution-scoped BigQuery table containing only delta rows."""
    client.delete_table(table_id, not_found_ok=True)
    table = bigquery.Table(table_id, schema=DOCUMENT_SCHEMA)
    table.description = "Meeting-scoped incremental Vertex AI Search import delta"
    client.create_table(table)
    if not documents:
        return
    config = bigquery.LoadJobConfig(
        schema=DOCUMENT_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    client.load_table_from_json(documents, table_id, job_config=config).result()
    loaded = client.get_table(table_id).num_rows
    if loaded != len(documents):
        raise RuntimeError(f"delta count mismatch: expected={len(documents)}, actual={loaded}")


def _publish_delta(
    client: bigquery.Client,
    sql_file: str,
    target_table: str,
    delta_table: str,
    meeting_ids: list[str],
) -> None:
    """Replace only requested meetings inside the authoritative document table."""
    sql = (SQL_DIR / sql_file).read_text(encoding="utf-8").format(
        target_table=target_table,
        delta_table=delta_table,
    )
    config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("meeting_ids", "STRING", meeting_ids)
        ]
    )
    client.query(sql, job_config=config).result()


def build_search_documents(
    client: bigquery.Client,
    project: str,
    dataset: str,
    meeting_ids: list[str],
    delta_table: str,
) -> int:
    """Generate and publish utterance documents for only the selected meetings."""
    sql, config = source_query(project, dataset, meeting_ids)
    documents = list(generate_documents(client.query(sql, job_config=config).result(page_size=5000)))
    _validate_documents(documents, set(meeting_ids))
    _load_delta(client, delta_table, documents)
    _publish_delta(
        client,
        "merge_search_documents.sql",
        f"{project}.{dataset}.search_documents",
        delta_table,
        meeting_ids,
    )
    return len(documents)


def _vote_inputs(
    client: bigquery.Client,
    project: str,
    dataset: str,
    meeting_ids: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    prefix = f"{project}.{dataset}"
    config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("meeting_ids", "STRING", meeting_ids)
        ]
    )
    meetings = {
        row.meeting_id: dict(row.items())
        for row in client.query(
            f"""
            SELECT meeting_id, meeting_date, meeting_type, committee_name, raw_pdf_gcs_uri
            FROM `{prefix}.meetings`
            WHERE meeting_id IN UNNEST(@meeting_ids)
              AND meeting_type = 'plenary'
              AND raw_pdf_gcs_uri IS NOT NULL
            """,
            job_config=config,
        ).result()
    }
    pages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not meetings:
        return meetings, pages
    page_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("meeting_ids", "STRING", list(meetings))
        ]
    )
    for row in client.query(
        f"""
        SELECT meeting_id, page_number, extracted_text
        FROM `{prefix}.pdf_pages`
        WHERE meeting_id IN UNNEST(@meeting_ids)
        ORDER BY meeting_id, page_number
        """,
        job_config=page_config,
    ).result(page_size=5000):
        pages[row.meeting_id].append(dict(row.items()))
    return meetings, pages


def build_vote_documents(
    client: bigquery.Client,
    project: str,
    dataset: str,
    meeting_ids: list[str],
    delta_table: str,
) -> tuple[int, int]:
    """Generate validated roll-call documents for selected plenary meetings."""
    prefix = f"{project}.{dataset}"
    names_to_ids: dict[str, list[str]] = defaultdict(list)
    for row in client.query(
        f"SELECT legislator_id, name FROM `{prefix}.legislators` ORDER BY legislator_id"
    ).result():
        names_to_ids[compact(row.name)].append(row.legislator_id)
    if not names_to_ids:
        raise RuntimeError("legislators table is empty; vote identity matching is unavailable")

    pattern = member_pattern(list(names_to_ids))
    meetings, pages_by_meeting = _vote_inputs(
        client, project, dataset, meeting_ids
    )
    documents: list[dict[str, str]] = []
    rejected_count = 0
    for meeting_id, meeting in meetings.items():
        valid, rejected = parse_meeting(
            meeting,
            pages_by_meeting.get(meeting_id, []),
            names_to_ids,
            pattern,
        )
        rejected_count += len(rejected)
        for vote in valid:
            documents.extend(documents_for_vote(vote, meeting, names_to_ids))

    _validate_documents(documents, set(meeting_ids))
    _load_delta(client, delta_table, documents)
    _publish_delta(
        client,
        "merge_vote_search_documents.sql",
        f"{project}.{dataset}.vote_search_documents",
        delta_table,
        meeting_ids,
    )
    return len(documents), rejected_count


def delete_tables(client: bigquery.Client, table_ids: Iterable[str]) -> None:
    for table_id in table_ids:
        client.delete_table(table_id, not_found_ok=True)

