from google.cloud import bigquery

from rag.config import BQ_PROJECT_ID, BQ_DATASET


client = bigquery.Client()


def resolve_legislator(name: str) -> list[dict]:
    query = f"""
        SELECT
            legislator_id,
            name,
            party_name,
            district
        FROM `{BQ_PROJECT_ID}.{BQ_DATASET}.legislators`
        WHERE name = @name
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("name", "STRING", name)
        ]
    )

    rows = client.query(
        query,
        job_config=job_config,
    ).result()

    return [dict(row.items()) for row in rows]

def get_utterances(utterance_ids: list[str]) -> list[dict]:
    if not utterance_ids:
        return []

    query = f"""
        SELECT
            u.utterance_id,
            u.speaker_name,
            u.speaker_position,
            u.legislator_id,
            u.utterance_text,
            u.page_start,
            u.page_end,
            u.source_pdf_gcs_uri,
            m.meeting_date,
            m.title AS meeting_title,
            m.committee_name,
            m.pdf_url AS source_pdf_url
        FROM UNNEST(@utterance_ids) AS requested_id WITH OFFSET AS request_order
        JOIN `{BQ_PROJECT_ID}.{BQ_DATASET}.utterances` AS u
            ON u.utterance_id = requested_id
        JOIN `{BQ_PROJECT_ID}.{BQ_DATASET}.meetings` AS m
            USING (meeting_id)
        ORDER BY
            request_order,
            u.sequence_no
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter(
                "utterance_ids",
                "STRING",
                utterance_ids,
            )
        ]
    )

    rows = client.query(
        query,
        job_config=job_config,
    ).result()

    return [dict(row.items()) for row in rows]
