#!/usr/bin/env python3
"""Build Vertex AI Search documents from validated Assembly utterances.

The source-of-truth tables are never modified. Documents are loaded into a
staging table and published to ``search_documents`` only after local checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterator

from google.cloud import bigquery


DOCUMENT_VERSION = "assembly-search-pdf-v2"
MAX_CHARS = 1800
MIN_BOUNDARY_CHARS = 1200
SHORT_UTTERANCE_CHARS = 30
CONTEXT_CHARS = 500


@dataclass(frozen=True)
class TextChunk:
    index: int
    start: int
    end: int
    text: str


def _boundary(text: str, start: int, hard_end: int) -> int:
    """Prefer a paragraph or sentence end near the hard character limit."""
    if hard_end >= len(text):
        return len(text)
    lower = min(start + MIN_BOUNDARY_CHARS, hard_end)
    candidates: list[int] = []
    for marker in ("\n", ".", "?", "!"):
        position = text.rfind(marker, lower, hard_end)
        if position >= lower:
            candidates.append(position + 1)
    return max(candidates) if candidates else hard_end


def split_text(text: str) -> list[TextChunk]:
    """Split without changing source characters; every character is covered once."""
    if not text:
        return []
    chunks: list[TextChunk] = []
    start = 0
    while start < len(text):
        end = _boundary(text, start, min(start + MAX_CHARS, len(text)))
        if end <= start:
            end = min(start + MAX_CHARS, len(text))
        chunks.append(TextChunk(len(chunks) + 1, start, end, text[start:end]))
        start = end
    return chunks


def limited_context(speaker: str | None, text: str | None, *, tail: bool) -> str | None:
    """검색 문서에 넣을 이전·다음 발언 문맥을 제한된 길이로 만든다."""
    if not text:
        return None
    if len(text) <= CONTEXT_CHARS:
        value = text
    else:
        value = text[-CONTEXT_CHARS:] if tail else text[:CONTEXT_CHARS]
    return f"{speaker or '발언자 미상'}: {value}"


def json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def document_id(utterance_id: str, chunk_index: int) -> str:
    """발언 ID와 청크 번호로 재실행에도 안정적인 검색 문서 ID를 만든다."""
    digest = hashlib.sha256(
        f"{DOCUMENT_VERSION}:{utterance_id}:{chunk_index}".encode("utf-8")
    ).hexdigest()[:48]
    return f"sd_{digest}"


def build_document(row: Any, chunk: TextChunk, chunk_count: int) -> dict[str, str]:
    """발언 청크와 회의·발언자·PDF 근거를 Vertex Search 문서로 변환한다."""
    speaker = row.speaker_name or row.speaker_label or "발언자 미상"
    short = len(row.utterance_text) < SHORT_UTTERANCE_CHARS
    before = limited_context(
        row.previous_speaker_name, row.previous_utterance_text, tail=True
    )
    after = limited_context(row.next_speaker_name, row.next_utterance_text, tail=False)
    source_agenda_ids = list(row.agenda_ids or [])
    source_agenda_titles = list(row.agenda_titles or [])
    direct_agenda_link = row.agenda_link_method == "html_context"
    agenda_ids = source_agenda_ids if direct_agenda_link else []
    agenda_titles = source_agenda_titles if direct_agenda_link else []
    if agenda_titles:
        agenda_label = "; ".join(agenda_titles)
        agenda_scope = "DIRECT"
    elif row.agenda_link_method == "explicit_range":
        agenda_label = (
            f"{len(source_agenda_ids)}개 안건 일괄 심사 "
            "(개별 발언의 특정 안건 귀속 미확정)"
        )
        agenda_scope = "RANGE"
    else:
        agenda_label = "안건 연결 미확정"
        agenda_scope = "UNRESOLVED"
    retrieval_parts = [
        f"회의: {row.meeting_title}",
        f"일자: {row.meeting_date}",
        f"위원회: {row.committee_name or '본회의'}",
        f"관련 안건: {agenda_label}",
        f"중심 발언자: {speaker} ({row.speaker_position or '직책 미상'})",
    ]
    if short and before:
        retrieval_parts.extend(["[직전 발언 문맥]", before])
    retrieval_parts.extend(["[중심 발언]", f"{speaker}: {chunk.text}"])
    if short and after:
        retrieval_parts.extend(["[직후 발언 문맥]", after])

    payload = {
        "schema_version": DOCUMENT_VERSION,
        "document_type": "assembly_utterance",
        "title": f"{speaker} - {row.meeting_title}",
        "content": chunk.text,
        "retrieval_text": "\n".join(retrieval_parts),
        "meeting_id": row.meeting_id,
        "meeting_title": row.meeting_title,
        "meeting_date": row.meeting_date,
        "meeting_type": row.meeting_type,
        "committee_name": row.committee_name,
        "assembly_no": row.assembly_no,
        "primary_utterance_id": row.utterance_id,
        "source_block_ids": [],
        "sequence_no": row.sequence_no,
        "chunk_index": chunk.index,
        "chunk_count": chunk_count,
        "char_start": chunk.start,
        "char_end": chunk.end,
        "speaker_name": row.speaker_name,
        "speaker_label": row.speaker_label,
        "speaker_position": row.speaker_position,
        "source_speaker_id": row.source_speaker_id,
        "legislator_id": row.legislator_id,
        "identity_status": "MATCHED" if row.legislator_id else "UNRESOLVED",
        "agenda_ids": agenda_ids,
        "agenda_titles": agenda_titles,
        "agenda_link_method": row.agenda_link_method,
        "agenda_scope": agenda_scope,
        "source_agenda_count": len(source_agenda_ids),
        "is_short_utterance": short,
        "context_before": before if short else None,
        "context_after": after if short else None,
        "source_anchor": row.source_anchor,
        "page_start": row.page_start,
        "page_end": row.page_end,
        "source_html_gcs_uri": None,
        "source_pdf_gcs_uri": row.raw_pdf_gcs_uri,
        "source_html_url": row.official_url,
        "source_pdf_url": row.pdf_url,
        "utterance_content_sha256": row.content_sha256,
        "chunk_content_sha256": hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
        "parser_version": row.parser_version,
    }
    return {
        "id": document_id(row.utterance_id, chunk.index),
        "jsonData": json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), default=json_default
        ),
    }


def source_query(
    project: str,
    dataset: str,
    meeting_ids: list[str],
    month_start: date | None = None,
) -> tuple[str, bigquery.QueryJobConfig]:
    """전체 또는 지정 범위의 발언과 주변 문맥을 읽는 BigQuery SQL을 만든다."""
    where = ""
    parameters: list[bigquery.QueryParameter] = []
    if meeting_ids:
        where = "WHERE u.meeting_id IN UNNEST(@meeting_ids)"
        parameters.append(bigquery.ArrayQueryParameter("meeting_ids", "STRING", meeting_ids))
    elif month_start is not None:
        where = (
            "WHERE u.meeting_date >= @month_start "
            "AND u.meeting_date < DATE_ADD(@month_start, INTERVAL 1 MONTH)"
        )
        parameters.append(bigquery.ScalarQueryParameter("month_start", "DATE", month_start))
    sql = f"""
        WITH contextual AS (
          SELECT
            u.*,
            LAG(u.speaker_name) OVER meeting_order AS previous_speaker_name,
            LAG(u.utterance_text) OVER meeting_order AS previous_utterance_text,
            LEAD(u.speaker_name) OVER meeting_order AS next_speaker_name,
            LEAD(u.utterance_text) OVER meeting_order AS next_utterance_text
          FROM `{project}.{dataset}.utterances` AS u
          {where}
          WINDOW meeting_order AS (PARTITION BY meeting_id ORDER BY sequence_no)
        ),
        agenda_title_map AS (
          SELECT
            u.utterance_id,
            ARRAY_AGG(a.title IGNORE NULLS ORDER BY a.agenda_no) AS agenda_titles
          FROM contextual AS u
          LEFT JOIN UNNEST(u.agenda_ids) AS agenda_id
          LEFT JOIN `{project}.{dataset}.agendas` AS a USING (agenda_id)
          GROUP BY u.utterance_id
        )
        SELECT
          u.*,
          m.assembly_no,
          m.title AS meeting_title,
          m.official_url,
          m.pdf_url,
          m.raw_pdf_gcs_uri,
          u.parser_version,
          agenda_title_map.agenda_titles
        FROM contextual AS u
        JOIN `{project}.{dataset}.meetings` AS m USING (meeting_id)
        LEFT JOIN agenda_title_map USING (utterance_id)
        ORDER BY u.meeting_id, u.sequence_no
    """
    return sql, bigquery.QueryJobConfig(query_parameters=parameters)


def generate_documents(rows: Iterator[Any]) -> Iterator[dict[str, str]]:
    """발언 행을 손실 없이 청킹하여 검색 문서를 순차 생성한다."""
    for row in rows:
        chunks = split_text(row.utterance_text)
        for chunk in chunks:
            yield build_document(row, chunk, len(chunks))


def run_self_tests() -> None:
    """청크 결합 시 원문이 정확히 복원되는지 로컬 자체 테스트한다."""
    samples = [
        "예.",
        "가" * (MAX_CHARS + 1),
        ("첫 문장입니다.\n" * 200) + "마지막 문장입니다.",
    ]
    for sample in samples:
        chunks = split_text(sample)
        assert chunks
        assert "".join(chunk.text for chunk in chunks) == sample
        assert all(chunk.end - chunk.start <= MAX_CHARS for chunk in chunks)
        assert [chunk.index for chunk in chunks] == list(range(1, len(chunks) + 1))


def parse_args() -> argparse.Namespace:
    """검색 문서 생성 범위와 점검 전용 옵션을 읽는다."""
    parser = argparse.ArgumentParser(description="Build Vertex AI Search documents")
    parser.add_argument("--project", default="proj-aj04-211200020328")
    parser.add_argument("--dataset", default="assembly")
    parser.add_argument("--meeting-ids", nargs="*", default=[])
    parser.add_argument("--self-test-only", action="store_true")
    parser.add_argument(
        "--repair-schema-only",
        action="store_true",
        help="Republish the current documents with the explicit import schema",
    )
    return parser.parse_args()


def publish_staging(
    client: bigquery.Client, staging: str, target: str, expected_count: int
) -> None:
    """Copy validated staging data so REQUIRED column modes are preserved."""
    copy_config = bigquery.CopyJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE
    )
    client.copy_table(staging, target, job_config=copy_config).result()
    published = client.get_table(target)
    if published.num_rows != expected_count:
        raise RuntimeError(
            f"published count mismatch: expected={expected_count}, actual={published.num_rows}"
        )
    published.description = (
        "Vertex AI Search structured-data import documents (id, jsonData)"
    )
    client.update_table(published, ["description"])


def main() -> int:
    """월별 스테이징을 완성한 뒤 전체 search_documents를 게시한다."""
    args = parse_args()
    run_self_tests()
    if args.self_test_only:
        print("self-tests passed")
        return 0

    client = bigquery.Client(project=args.project)
    target = f"{args.project}.{args.dataset}.search_documents"
    staging = f"{args.project}.{args.dataset}.search_documents_staging"
    schema = [
        bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("jsonData", "STRING", mode="NULLABLE"),
    ]
    table = bigquery.Table(staging, schema=schema)
    table.description = "검증 후 Vertex AI Search로 가져올 발언 문맥 문서 스테이징"
    client.delete_table(staging, not_found_ok=True)
    client.create_table(table)

    if args.repair_schema_only:
        client.query(
            f"INSERT INTO `{staging}` (id, jsonData) "
            f"SELECT id, jsonData FROM `{target}`"
        ).result()
        repaired_count = client.get_table(staging).num_rows
        publish_staging(client, staging, target, repaired_count)
        client.delete_table(staging)
        print(f"repaired schema for {repaired_count:,} documents -> {target}")
        return 0

    count = 0
    max_json_bytes = 0
    scope_staging = f"{args.project}.{args.dataset}.search_documents_scope_staging"
    if args.meeting_ids:
        scopes: list[tuple[str, date | None]] = [("selected meetings", None)]
    else:
        month_rows = client.query(
            f"SELECT DISTINCT DATE_TRUNC(meeting_date, MONTH) month_start "
            f"FROM `{args.project}.{args.dataset}.utterances` ORDER BY month_start"
        ).result()
        scopes = [(row.month_start.isoformat(), row.month_start) for row in month_rows]

    for scope_index, (scope_name, month_start) in enumerate(scopes, start=1):
        client.delete_table(scope_staging, not_found_ok=True)
        client.create_table(bigquery.Table(scope_staging, schema=schema))
        scope_count = 0
        scope_max_bytes = 0
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                with tempfile.NamedTemporaryFile(mode="w+b", suffix=".ndjson") as stream:
                    sql, job_config = source_query(
                        args.project, args.dataset, args.meeting_ids, month_start
                    )
                    rows = client.query(sql, job_config=job_config).result(page_size=5000)
                    scope_count = 0
                    scope_max_bytes = 0
                    for document in generate_documents(rows):
                        encoded = (
                            json.dumps(document, ensure_ascii=False, separators=(",", ":"))
                            + "\n"
                        ).encode("utf-8")
                        stream.write(encoded)
                        scope_count += 1
                        scope_max_bytes = max(
                            scope_max_bytes, len(document["jsonData"].encode("utf-8"))
                        )
                    stream.flush()
                    stream.seek(0)
                    load_config = bigquery.LoadJobConfig(
                        schema=schema,
                        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                    )
                    client.load_table_from_file(
                        stream, scope_staging, job_config=load_config
                    ).result()
                    loaded_scope_count = client.get_table(scope_staging).num_rows
                    if loaded_scope_count != scope_count:
                        raise RuntimeError(
                            f"scope count mismatch: generated={scope_count}, "
                            f"loaded={loaded_scope_count}"
                        )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                print(
                    f"scope {scope_name} attempt {attempt}/3 failed: {exc}",
                    flush=True,
                )
                if attempt < 3:
                    time.sleep(2**attempt)
        if last_error is not None:
            raise RuntimeError(f"scope {scope_name} failed after retries") from last_error
        client.query(
            f"""
            MERGE `{staging}` AS target
            USING `{scope_staging}` AS source
            ON target.id = source.id
            WHEN NOT MATCHED THEN
              INSERT (id, jsonData) VALUES (source.id, source.jsonData)
            """
        ).result()
        count += scope_count
        max_json_bytes = max(max_json_bytes, scope_max_bytes)
        print(
            f"loaded scope {scope_index}/{len(scopes)} {scope_name}: "
            f"{scope_count:,} documents (total {count:,})",
            flush=True,
        )
    client.delete_table(scope_staging, not_found_ok=True)

    staged_count = next(iter(client.query(f"SELECT COUNT(*) n FROM `{staging}`").result())).n
    if staged_count != count:
        raise RuntimeError(f"staging count mismatch: generated={count}, loaded={staged_count}")

    publish_staging(client, staging, target, count)
    client.delete_table(staging)
    print(f"published {count:,} documents -> {target}", flush=True)
    print(f"max jsonData bytes={max_json_bytes:,}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
