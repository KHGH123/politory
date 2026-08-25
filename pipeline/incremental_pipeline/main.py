#!/usr/bin/env python3
"""Collect and publish only newly discovered Assembly meetings."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from google.cloud import bigquery

from incremental_pipeline.documents import (
    build_search_documents,
    build_vote_documents,
    delete_tables,
)
from incremental_pipeline.vertex import import_delta


ROOT = Path(__file__).resolve().parent.parent
STATUS_TABLE = "incremental_meeting_status"


@dataclass(frozen=True)
class Settings:
    project: str
    dataset: str
    bucket: str
    assembly_no: int
    start_date: date
    end_date: date
    meeting_types: tuple[str, ...]
    search_data_store_id: str | None
    vote_data_store_id: str | None
    vertex_project: str
    vertex_location: str
    vertex_timeout_seconds: int
    speaker_request_delay: float
    speaker_fetch_attempts: int
    speaker_max_consecutive_failures: int
    apply: bool
    keep_delta_tables: bool


def parse_args() -> Settings:
    parser = argparse.ArgumentParser(
        description="Incrementally collect new Assembly meetings and publish search deltas"
    )
    parser.add_argument("--project", default=os.getenv("GCP_PROJECT", "proj-aj04-211200020328"))
    parser.add_argument("--dataset", default=os.getenv("BQ_DATASET", "assembly"))
    parser.add_argument(
        "--bucket",
        default=os.getenv("ASSEMBLY_BUCKET", "proj-aj04-211200020328-assembly-us"),
    )
    parser.add_argument("--assembly-no", type=int, default=22)
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument(
        "--meeting-types",
        nargs="+",
        choices=("plenary", "committee"),
        default=("plenary", "committee"),
    )
    parser.add_argument(
        "--search-data-store-id", default=os.getenv("VERTEX_SEARCH_DATA_STORE_ID")
    )
    parser.add_argument(
        "--vote-data-store-id", default=os.getenv("VERTEX_VOTE_DATA_STORE_ID")
    )
    parser.add_argument(
        "--vertex-project",
        default=os.getenv("VERTEX_PROJECT", "proj-aj36-211200020328"),
    )
    parser.add_argument(
        "--vertex-location", default=os.getenv("VERTEX_SEARCH_LOCATION", "global")
    )
    parser.add_argument("--vertex-timeout-seconds", type=int, default=3600)
    parser.add_argument(
        "--speaker-request-delay",
        type=float,
        default=float(os.getenv("SPEAKER_REQUEST_DELAY", "1.5")),
    )
    parser.add_argument(
        "--speaker-fetch-attempts",
        type=int,
        default=int(os.getenv("SPEAKER_FETCH_ATTEMPTS", "5")),
    )
    parser.add_argument(
        "--speaker-max-consecutive-failures",
        type=int,
        default=int(os.getenv("SPEAKER_MAX_CONSECUTIVE_FAILURES", "3")),
    )
    parser.add_argument("--keep-delta-tables", action="store_true")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform GCP writes; without this flag only the execution plan is printed",
    )
    args = parser.parse_args()
    if args.lookback_days < 1:
        parser.error("--lookback-days must be at least 1")
    if args.speaker_request_delay < 0:
        parser.error("--speaker-request-delay must be non-negative")
    if args.speaker_fetch_attempts < 1:
        parser.error("--speaker-fetch-attempts must be at least 1")
    if args.speaker_max_consecutive_failures < 1:
        parser.error("--speaker-max-consecutive-failures must be at least 1")
    end_date = args.end_date or date.today()
    start_date = args.start_date or (end_date - timedelta(days=args.lookback_days - 1))
    if start_date > end_date:
        parser.error("--start-date must not be after --end-date")
    return Settings(
        project=args.project,
        dataset=args.dataset,
        bucket=args.bucket,
        assembly_no=args.assembly_no,
        start_date=start_date,
        end_date=end_date,
        meeting_types=tuple(args.meeting_types),
        search_data_store_id=args.search_data_store_id,
        vote_data_store_id=args.vote_data_store_id,
        vertex_project=args.vertex_project,
        vertex_location=args.vertex_location,
        vertex_timeout_seconds=args.vertex_timeout_seconds,
        speaker_request_delay=args.speaker_request_delay,
        speaker_fetch_attempts=args.speaker_fetch_attempts,
        speaker_max_consecutive_failures=args.speaker_max_consecutive_failures,
        apply=args.apply,
        keep_delta_tables=args.keep_delta_tables,
    )


def date_ranges_by_year(start: date, end: date) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        year_end = min(date(cursor.year, 12, 31), end)
        ranges.append((cursor, year_end))
        cursor = year_end + timedelta(days=1)
    return ranges


def collector_command(settings: Settings, start: date, end: date) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "step01_collect_assembly.py"),
        "--year",
        str(start.year),
        "--start-date",
        start.isoformat(),
        "--end-date",
        end.isoformat(),
        "--meeting-types",
        *settings.meeting_types,
        "--project",
        settings.project,
        "--dataset",
        settings.dataset,
        "--bucket",
        settings.bucket,
        "--assembly-no",
        str(settings.assembly_no),
    ]


def rebuild_command(settings: Settings, meeting_id: str) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "step02_rebuild_pdf_tables.py"),
        "--project",
        settings.project,
        "--dataset",
        settings.dataset,
        "--bucket",
        settings.bucket,
        "--assembly-no",
        str(settings.assembly_no),
        "--meeting-id",
        meeting_id,
        "--apply",
    ]


def speaker_enrichment_command(
    settings: Settings, meeting_ids: list[str]
) -> list[str]:
    command = [
        sys.executable,
        str(ROOT / "step08_enrich_speaker_ids.py"),
        "--project",
        settings.project,
        "--dataset",
        settings.dataset,
        "--bucket",
        settings.bucket,
        "--assembly-no",
        str(settings.assembly_no),
        "--workers",
        "1",
        "--request-delay",
        str(settings.speaker_request_delay),
        "--fetch-attempts",
        str(settings.speaker_fetch_attempts),
        "--max-consecutive-source-failures",
        str(settings.speaker_max_consecutive_failures),
        "--apply",
        "--skip-search-documents",
        "--fail-on-rejected",
    ]
    for meeting_id in meeting_ids:
        command.extend(("--meeting-id", meeting_id))
    return command


def run_command(command: list[str]) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def ensure_status_table(client: bigquery.Client, settings: Settings) -> str:
    table = f"{settings.project}.{settings.dataset}.{STATUS_TABLE}"
    client.query(
        f"""
        CREATE TABLE IF NOT EXISTS `{table}` (
          meeting_id STRING NOT NULL,
          pipeline_status STRING NOT NULL,
          pipeline_stage STRING,
          run_id STRING,
          attempt_count INT64,
          last_error STRING,
          first_seen_at TIMESTAMP,
          updated_at TIMESTAMP
        )
        CLUSTER BY pipeline_status, meeting_id
        OPTIONS(description = 'Cloud Run 신규 회의 증분 처리 상태')
        ;

        ALTER TABLE `{table}`
        ADD COLUMN IF NOT EXISTS pipeline_stage STRING
        """
    ).result()
    return table


def meeting_ids_in_range(client: bigquery.Client, settings: Settings) -> set[str]:
    config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("start_date", "DATE", settings.start_date),
            bigquery.ScalarQueryParameter("end_date", "DATE", settings.end_date),
        ]
    )
    rows = client.query(
        f"""
        SELECT meeting_id
        FROM `{settings.project}.{settings.dataset}.meetings`
        WHERE meeting_date BETWEEN @start_date AND @end_date
        """,
        job_config=config,
    ).result()
    return {row.meeting_id for row in rows}


def record_pending(
    client: bigquery.Client,
    status_table: str,
    meeting_ids: list[str],
    run_id: str,
) -> None:
    if not meeting_ids:
        return
    config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("meeting_ids", "STRING", meeting_ids),
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
        ]
    )
    client.query(
        f"""
        MERGE `{status_table}` target
        USING (SELECT meeting_id FROM UNNEST(@meeting_ids) meeting_id) source
        ON target.meeting_id = source.meeting_id
        WHEN MATCHED THEN UPDATE SET
          pipeline_status = 'PENDING', run_id = @run_id,
          pipeline_stage = 'DISCOVERED',
          attempt_count = COALESCE(attempt_count, 0) + 1,
          last_error = NULL, updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT
          (meeting_id, pipeline_status, pipeline_stage, run_id, attempt_count, first_seen_at, updated_at)
        VALUES
          (source.meeting_id, 'PENDING', 'DISCOVERED', @run_id, 1, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
        """,
        job_config=config,
    ).result()


def pending_or_failed_ids(client: bigquery.Client, status_table: str) -> set[str]:
    rows = client.query(
        f"SELECT meeting_id FROM `{status_table}` WHERE pipeline_status != 'SUCCESS'"
    ).result()
    return {row.meeting_id for row in rows}


def untracked_without_search_documents(
    client: bigquery.Client,
    settings: Settings,
    status_table: str,
) -> set[str]:
    """Recover collection successes lost before a PENDING status was recorded."""
    config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("start_date", "DATE", settings.start_date),
            bigquery.ScalarQueryParameter("end_date", "DATE", settings.end_date),
        ]
    )
    rows = client.query(
        f"""
        SELECT m.meeting_id
        FROM `{settings.project}.{settings.dataset}.meetings` m
        LEFT JOIN `{status_table}` s USING (meeting_id)
        LEFT JOIN `{settings.project}.{settings.dataset}.search_documents` d
          ON JSON_VALUE(d.jsonData, '$.meeting_id') = m.meeting_id
        WHERE m.meeting_date BETWEEN @start_date AND @end_date
          AND s.meeting_id IS NULL
        GROUP BY m.meeting_id
        HAVING COUNT(d.id) = 0
        """,
        job_config=config,
    ).result()
    return {row.meeting_id for row in rows}


def update_status(
    client: bigquery.Client,
    status_table: str,
    meeting_ids: list[str],
    status: str,
    error: str | None = None,
    stage: str | None = None,
) -> None:
    if not meeting_ids:
        return
    config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("meeting_ids", "STRING", meeting_ids),
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("error", "STRING", error),
            bigquery.ScalarQueryParameter("stage", "STRING", stage),
        ]
    )
    client.query(
        f"""
        UPDATE `{status_table}`
        SET pipeline_status = @status, pipeline_stage = COALESCE(@stage, pipeline_stage),
            last_error = @error, updated_at = CURRENT_TIMESTAMP()
        WHERE meeting_id IN UNNEST(@meeting_ids)
        """,
        job_config=config,
    ).result()


def print_plan(settings: Settings) -> None:
    plan = {
        "mode": "DRY_RUN",
        "project": settings.project,
        "dataset": settings.dataset,
        "date_range": [settings.start_date.isoformat(), settings.end_date.isoformat()],
        "collector_commands": [
            collector_command(settings, start, end)
            for start, end in date_ranges_by_year(settings.start_date, settings.end_date)
        ],
        "vertex": {
            "project": settings.vertex_project,
            "search_data_store_configured": bool(settings.search_data_store_id),
            "vote_data_store_configured": bool(settings.vote_data_store_id),
            "location": settings.vertex_location,
        },
        "pipeline_stages": [
            "collect_new_meetings",
            "rebuild_pdf_tables",
            "enrich_speaker_ids",
            "build_bigquery_deltas",
            "vertex_incremental_import",
        ],
        "speaker_enrichment": {
            "workers": 1,
            "request_delay": settings.speaker_request_delay,
            "fetch_attempts": settings.speaker_fetch_attempts,
            "max_consecutive_failures": settings.speaker_max_consecutive_failures,
            "failure_is_fatal": True,
        },
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    print("DRY RUN: no API calls or GCP reads/writes were performed.")


def main() -> int:
    settings = parse_args()
    if not settings.apply:
        print_plan(settings)
        return 0
    if not os.getenv("ASSEMBLY_API_KEY"):
        raise SystemExit("ASSEMBLY_API_KEY is required when --apply is used")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
    client = bigquery.Client(project=settings.project)
    status_table = ensure_status_table(client, settings)
    before = meeting_ids_in_range(client, settings)
    for start, end in date_ranges_by_year(settings.start_date, settings.end_date):
        run_command(collector_command(settings, start, end))
    after = meeting_ids_in_range(client, settings)
    new_ids = sorted(after - before)

    # Failed meetings remain eligible even after they move outside the discovery window.
    target_ids = sorted(
        set(new_ids)
        | pending_or_failed_ids(client, status_table)
        | untracked_without_search_documents(client, settings, status_table)
    )
    if not target_ids:
        print("No new or retryable meetings were found.")
        return 0
    record_pending(client, status_table, target_ids, run_id)

    search_delta = f"{settings.project}.{settings.dataset}.search_delta_{run_id}"
    vote_delta = f"{settings.project}.{settings.dataset}.vote_delta_{run_id}"
    try:
        update_status(client, status_table, target_ids, "PENDING", stage="PDF_REBUILD")
        for meeting_id in target_ids:
            run_command(rebuild_command(settings, meeting_id))

        update_status(
            client, status_table, target_ids, "PENDING", stage="SPEAKER_IDENTITY"
        )
        run_command(speaker_enrichment_command(settings, target_ids))

        update_status(
            client, status_table, target_ids, "PENDING", stage="SEARCH_DOCUMENTS"
        )
        search_count = build_search_documents(
            client, settings.project, settings.dataset, target_ids, search_delta
        )
        vote_count, rejected_votes = build_vote_documents(
            client, settings.project, settings.dataset, target_ids, vote_delta
        )

        update_status(client, status_table, target_ids, "PENDING", stage="VERTEX_IMPORT")
        if search_count and settings.search_data_store_id:
            import_delta(
                settings.project,
                settings.dataset,
                search_delta.rsplit(".", 1)[-1],
                settings.search_data_store_id,
                settings.vertex_project,
                settings.vertex_location,
                settings.vertex_timeout_seconds,
            )
        if vote_count and settings.vote_data_store_id:
            import_delta(
                settings.project,
                settings.dataset,
                vote_delta.rsplit(".", 1)[-1],
                settings.vote_data_store_id,
                settings.vertex_project,
                settings.vertex_location,
                settings.vertex_timeout_seconds,
            )
        update_status(
            client, status_table, target_ids, "SUCCESS", stage="COMPLETE"
        )
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "meetings": target_ids,
                    "search_documents": search_count,
                    "vote_search_documents": vote_count,
                    "rejected_votes": rejected_votes,
                    "vertex_search_imported": bool(
                        search_count and settings.search_data_store_id
                    ),
                    "vertex_vote_imported": bool(
                        vote_count and settings.vote_data_store_id
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        update_status(
            client,
            status_table,
            target_ids,
            "FAILED",
            str(exc)[:4000],
        )
        raise
    finally:
        if not settings.keep_delta_tables:
            delete_tables(client, (search_delta, vote_delta))


if __name__ == "__main__":
    raise SystemExit(main())
