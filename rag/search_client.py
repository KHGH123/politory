from itertools import islice
from google.cloud import discoveryengine_v1 as discoveryengine

from rag.config import (
    SEARCH_PROJECT_ID,
    SEARCH_ENGINE_ID,
    SEARCH_LOCATION,
)


client = discoveryengine.SearchServiceClient()


def search_speeches(
    query: str,
    page_size: int = 10,
    filter_: str | None = None,
) -> list[dict]:

    serving_config = (
        f"projects/{SEARCH_PROJECT_ID}"
        f"/locations/{SEARCH_LOCATION}"
        f"/collections/default_collection"
        f"/engines/{SEARCH_ENGINE_ID}"
        f"/servingConfigs/default_config"
    )

    request = discoveryengine.SearchRequest(
        serving_config=serving_config,
        query=query,
        page_size=page_size,
        filter=filter_ or "",
    )

    response = client.search(
        request=request,
        timeout=15,
    )

    results = []

    for result in islice(response, page_size):
        document = result.document

        results.append({
            "id": document.id,
            "data": dict(document.struct_data),
        })

    return results