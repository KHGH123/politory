#!/usr/bin/env python3
"""Validate the published vote_search_documents table."""

import argparse
import json
from google.cloud import bigquery


def main() -> int:
    """표결 검색문서의 JSON·ID·인원 합계·PDF 연결을 전수 검사한다."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="proj-aj04-211200020328")
    parser.add_argument("--dataset", default="assembly")
    args = parser.parse_args()
    prefix = f"{args.project}.{args.dataset}"
    client = bigquery.Client(project=args.project)
    rows = client.query(f"""
      WITH docs AS (
        SELECT id, jsonData,
          JSON_VALUE(jsonData, '$.document_type') document_type,
          JSON_VALUE(jsonData, '$.vote_id') vote_id,
          JSON_VALUE(jsonData, '$.meeting_id') meeting_id,
          JSON_VALUE(jsonData, '$.vote_title') vote_title,
          SAFE_CAST(JSON_VALUE(jsonData, '$.vote_date') AS DATE) vote_date,
          JSON_VALUE(jsonData, '$.member_name') member_name,
          JSON_VALUE(jsonData, '$.choice') choice,
          SAFE_CAST(JSON_VALUE(jsonData, '$.total_count') AS INT64) total_count,
          SAFE_CAST(JSON_VALUE(jsonData, '$.yes_count') AS INT64) yes_count,
          SAFE_CAST(JSON_VALUE(jsonData, '$.no_count') AS INT64) no_count,
          SAFE_CAST(JSON_VALUE(jsonData, '$.abstain_count') AS INT64) abstain_count,
          SAFE_CAST(JSON_VALUE(jsonData, '$.page_start') AS INT64) page_start,
          SAFE_CAST(JSON_VALUE(jsonData, '$.page_end') AS INT64) page_end,
          JSON_VALUE(jsonData, '$.source_pdf_gcs_uri') source_pdf_gcs_uri
        FROM `{prefix}.vote_search_documents`
      ), metrics AS (
        SELECT 'documents' metric, COUNT(*) value FROM docs
        UNION ALL SELECT 'distinct_ids', COUNT(DISTINCT id) FROM docs
        UNION ALL SELECT 'invalid_json', COUNTIF(SAFE.PARSE_JSON(jsonData) IS NULL) FROM docs
        UNION ALL SELECT 'summary_documents', COUNTIF(document_type='assembly_vote_summary') FROM docs
        UNION ALL SELECT 'member_documents', COUNTIF(document_type='assembly_vote_member') FROM docs
        UNION ALL SELECT 'invalid_document_types', COUNTIF(document_type NOT IN ('assembly_vote_summary','assembly_vote_member') OR document_type IS NULL) FROM docs
        UNION ALL SELECT 'missing_vote_identity', COUNTIF(vote_id IS NULL OR meeting_id IS NULL OR vote_title IS NULL OR vote_date IS NULL) FROM docs
        UNION ALL SELECT 'invalid_member_choices', COUNTIF(document_type='assembly_vote_member' AND (member_name IS NULL OR choice NOT IN ('YES','NO','ABSTAIN'))) FROM docs
        UNION ALL SELECT 'invalid_counts', COUNTIF(total_count IS NULL OR yes_count IS NULL OR no_count IS NULL OR abstain_count IS NULL OR total_count != yes_count+no_count+abstain_count) FROM docs
        UNION ALL SELECT 'invalid_pages', COUNTIF(page_start IS NULL OR page_end < page_start) FROM docs
        UNION ALL SELECT 'meeting_pdf_mismatches', COUNT(*) FROM docs d LEFT JOIN `{prefix}.meetings` m USING(meeting_id) WHERE m.meeting_id IS NULL OR d.source_pdf_gcs_uri != m.raw_pdf_gcs_uri
        UNION ALL SELECT 'votes_without_one_summary', COUNT(*) FROM (SELECT vote_id FROM docs GROUP BY vote_id HAVING COUNTIF(document_type='assembly_vote_summary') != 1)
        UNION ALL SELECT 'vote_member_count_mismatches', COUNT(*) FROM (SELECT vote_id, ANY_VALUE(total_count) total_count, COUNTIF(document_type='assembly_vote_member') members FROM docs GROUP BY vote_id HAVING members != total_count)
      ) SELECT metric, value FROM metrics ORDER BY metric
    """).result()
    metrics = {row.metric: int(row.value) for row in rows}
    expected_zero = [
        "invalid_json", "invalid_document_types", "missing_vote_identity",
        "invalid_member_choices", "invalid_counts", "invalid_pages",
        "meeting_pdf_mismatches", "votes_without_one_summary",
        "vote_member_count_mismatches",
    ]
    failures = [name for name in expected_zero if metrics.get(name) != 0]
    if metrics.get("documents") != metrics.get("distinct_ids"):
        failures.append("duplicate_ids")
    result = {"table": f"{prefix}.vote_search_documents", "status": "PASS" if not failures else "FAIL", "metrics": metrics, "failures": failures}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
