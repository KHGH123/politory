#!/usr/bin/env python3
"""Run reproducible, full-table integrity checks for Vertex AI Search documents."""

from __future__ import annotations

import argparse
import json
import sys

from google.cloud import bigquery


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate assembly.search_documents")
    parser.add_argument("--project", default="proj-aj04-211200020328")
    parser.add_argument("--dataset", default="assembly")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = bigquery.Client(project=args.project)
    prefix = f"{args.project}.{args.dataset}"
    query = f"""
      WITH docs AS (
        SELECT
          id,
          jsonData,
          JSON_VALUE(jsonData, '$.primary_utterance_id') AS utterance_id,
          JSON_VALUE(jsonData, '$.meeting_id') AS meeting_id,
          JSON_VALUE(jsonData, '$.content') AS content,
          SAFE_CAST(JSON_VALUE(jsonData, '$.char_start') AS INT64) AS char_start,
          SAFE_CAST(JSON_VALUE(jsonData, '$.char_end') AS INT64) AS char_end,
          SAFE_CAST(JSON_VALUE(jsonData, '$.chunk_index') AS INT64) AS chunk_index,
          SAFE_CAST(JSON_VALUE(jsonData, '$.chunk_count') AS INT64) AS chunk_count,
          JSON_VALUE(jsonData, '$.chunk_content_sha256') AS chunk_sha256,
          JSON_VALUE_ARRAY(jsonData, '$.agenda_ids') AS agenda_ids,
          JSON_VALUE(jsonData, '$.agenda_link_method') AS agenda_link_method,
          JSON_VALUE(jsonData, '$.source_pdf_gcs_uri') AS source_pdf_gcs_uri,
          SAFE_CAST(JSON_VALUE(jsonData, '$.page_start') AS INT64) AS page_start,
          SAFE_CAST(JSON_VALUE(jsonData, '$.page_end') AS INT64) AS page_end,
          SAFE_CAST(JSON_VALUE(jsonData, '$.source_agenda_count') AS INT64)
            AS source_agenda_count
        FROM `{prefix}.search_documents`
      ),
      per_utterance AS (
        SELECT
          d.utterance_id,
          COUNT(*) AS doc_count,
          COUNT(DISTINCT d.chunk_index) AS distinct_chunk_indexes,
          MIN(d.chunk_index) AS min_chunk_index,
          MAX(d.chunk_index) AS max_chunk_index,
          MAX(d.chunk_count) AS declared_chunk_count,
          STRING_AGG(d.content, '' ORDER BY d.chunk_index) AS reconstructed_text,
          ANY_VALUE(u.utterance_text) AS source_text
        FROM docs AS d
        LEFT JOIN `{prefix}.utterances` AS u USING (utterance_id)
        GROUP BY d.utterance_id
      ),
      metrics AS (
        SELECT 'documents' AS metric, COUNT(*) AS value FROM docs
        UNION ALL SELECT 'distinct_document_ids', COUNT(DISTINCT id) FROM docs
        UNION ALL SELECT 'invalid_json', COUNTIF(SAFE.PARSE_JSON(jsonData) IS NULL) FROM docs
        UNION ALL SELECT 'invalid_document_ids', COUNTIF(NOT REGEXP_CONTAINS(id, r'^sd_[0-9a-f]{{48}}$')) FROM docs
        UNION ALL SELECT 'covered_utterances', COUNT(DISTINCT utterance_id) FROM docs
        UNION ALL SELECT 'source_utterances', COUNT(*) FROM `{prefix}.utterances`
        UNION ALL SELECT 'source_utterances_without_document', COUNT(*)
          FROM `{prefix}.utterances` AS u
          LEFT JOIN (SELECT DISTINCT utterance_id FROM docs) AS d USING (utterance_id)
          WHERE d.utterance_id IS NULL
        UNION ALL SELECT 'documents_without_source_utterance', COUNT(*)
          FROM docs AS d LEFT JOIN `{prefix}.utterances` AS u USING (utterance_id)
          WHERE u.utterance_id IS NULL
        UNION ALL SELECT 'content_substring_mismatches', COUNT(*)
          FROM docs AS d JOIN `{prefix}.utterances` AS u USING (utterance_id)
          WHERE d.content != SUBSTR(u.utterance_text, d.char_start + 1, d.char_end - d.char_start)
        UNION ALL SELECT 'chunk_hash_mismatches', COUNT(*) FROM docs
          WHERE chunk_sha256 != LOWER(TO_HEX(SHA256(content)))
        UNION ALL SELECT 'meeting_link_mismatches', COUNT(*)
          FROM docs AS d JOIN `{prefix}.utterances` AS u USING (utterance_id)
          WHERE d.meeting_id != u.meeting_id
        UNION ALL SELECT 'pdf_source_mismatches', COUNT(*)
          FROM docs AS d JOIN `{prefix}.utterances` AS u USING (utterance_id)
          WHERE d.source_pdf_gcs_uri != u.source_pdf_gcs_uri
             OR d.page_start != u.page_start OR d.page_end != u.page_end
        UNION ALL SELECT 'agenda_source_count_mismatches', COUNT(*)
          FROM docs AS d JOIN `{prefix}.utterances` AS u USING (utterance_id)
          WHERE d.source_agenda_count != ARRAY_LENGTH(u.agenda_ids)
             OR d.agenda_link_method != u.agenda_link_method
        UNION ALL SELECT 'direct_agenda_id_mismatches', COUNT(*)
          FROM docs AS d JOIN `{prefix}.utterances` AS u USING (utterance_id)
          WHERE u.agenda_link_method = 'html_context'
            AND TO_JSON_STRING(d.agenda_ids) != TO_JSON_STRING(u.agenda_ids)
        UNION ALL SELECT 'non_direct_agenda_ids_exposed', COUNT(*)
          FROM docs
          WHERE agenda_link_method != 'html_context' AND ARRAY_LENGTH(agenda_ids) > 0
        UNION ALL SELECT 'invalid_char_ranges', COUNT(*) FROM docs
          WHERE char_start IS NULL OR char_end IS NULL OR char_start < 0
             OR char_end <= char_start OR char_end - char_start > 1800
        UNION ALL SELECT 'chunk_declaration_errors', COUNT(*) FROM per_utterance
          WHERE doc_count != declared_chunk_count
             OR distinct_chunk_indexes != doc_count
             OR min_chunk_index != 1 OR max_chunk_index != doc_count
        UNION ALL SELECT 'reconstructed_text_mismatches', COUNT(*) FROM per_utterance
          WHERE reconstructed_text != source_text
        UNION ALL SELECT 'max_content_chars', COALESCE(MAX(LENGTH(content)), 0) FROM docs
        UNION ALL SELECT 'max_json_bytes', COALESCE(MAX(BYTE_LENGTH(jsonData)), 0) FROM docs
        UNION ALL SELECT 'multi_chunk_utterances', COUNTIF(doc_count > 1) FROM per_utterance
        UNION ALL SELECT 'max_chunks_per_utterance', COALESCE(MAX(doc_count), 0) FROM per_utterance
      )
      SELECT metric, value FROM metrics ORDER BY metric
    """
    metrics = {row.metric: row.value for row in client.query(query).result()}

    schema_query = f"""
      SELECT column_name, data_type, is_nullable, ordinal_position
      FROM `{args.project}.{args.dataset}.INFORMATION_SCHEMA.COLUMNS`
      WHERE table_name = 'search_documents'
      ORDER BY ordinal_position
    """
    schema = [dict(row.items()) for row in client.query(schema_query).result()]

    expected_zero = [
        "invalid_json",
        "invalid_document_ids",
        "source_utterances_without_document",
        "documents_without_source_utterance",
        "content_substring_mismatches",
        "chunk_hash_mismatches",
        "meeting_link_mismatches",
        "pdf_source_mismatches",
        "agenda_source_count_mismatches",
        "direct_agenda_id_mismatches",
        "non_direct_agenda_ids_exposed",
        "invalid_char_ranges",
        "chunk_declaration_errors",
        "reconstructed_text_mismatches",
    ]
    failures = [name for name in expected_zero if metrics.get(name) != 0]
    if metrics.get("documents") != metrics.get("distinct_document_ids"):
        failures.append("duplicate_document_ids")
    if metrics.get("covered_utterances") != metrics.get("source_utterances"):
        failures.append("utterance_coverage")
    if schema != [
        {"column_name": "id", "data_type": "STRING", "is_nullable": "NO", "ordinal_position": 1},
        {"column_name": "jsonData", "data_type": "STRING", "is_nullable": "YES", "ordinal_position": 2},
    ]:
        failures.append("table_schema")

    result = {
        "table": f"{prefix}.search_documents",
        "status": "PASS" if not failures else "FAIL",
        "metrics": metrics,
        "schema": schema,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
