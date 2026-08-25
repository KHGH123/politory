"""Optional Vertex AI Search incremental imports from BigQuery delta tables."""

from __future__ import annotations

def import_delta(
    source_project: str,
    dataset: str,
    table_id: str,
    data_store_id: str,
    vertex_project: str,
    location: str = "global",
    timeout_seconds: int = 3600,
) -> None:
    """Upsert one BigQuery delta table into an existing Vertex data store."""
    # Keep this dependency lazy so local dry-runs do not require the Vertex SDK.
    from google.cloud import discoveryengine_v1 as discoveryengine

    client = discoveryengine.DocumentServiceClient()
    parent = client.branch_path(
        project=vertex_project,
        location=location,
        data_store=data_store_id,
        branch="default_branch",
    )
    request = discoveryengine.ImportDocumentsRequest(
        parent=parent,
        bigquery_source=discoveryengine.BigQuerySource(
            project_id=source_project,
            dataset_id=dataset,
            table_id=table_id,
            data_schema="custom",
        ),
        reconciliation_mode=(
            discoveryengine.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL
        ),
    )
    client.import_documents(request=request).result(timeout=timeout_seconds)
