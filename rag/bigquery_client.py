from google.cloud import bigquery

from rag.config import SOURCE_PROJECT_ID


client = bigquery.Client()


def resolve_legislator(name: str) -> list[dict]:
    query = f"""
    SELECT
      legislator_id,
      name,
      party_name,
      district
    FROM `{SOURCE_PROJECT_ID}.assembly.legislators`
    WHERE name = @name
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("name", "STRING", name)
        ]
    )

    rows = client.query(query, job_config=job_config).result()

    return [dict(row.items()) for row in rows]