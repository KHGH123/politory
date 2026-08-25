from itertools import islice
from google.cloud import discoveryengine_v1 as discoveryengine

from rag.config import (
    ACTION_SEARCH_ENGINE_ID,
    SEARCH_PROJECT_ID,
    SEARCH_ENGINE_ID,
    SEARCH_LOCATION,
)


client = discoveryengine.SearchServiceClient()


def _search(
    *,
    query: str,
    project_id: str,
    location: str,
    engine_id: str,
    page_size: int,
    filter_: str | None,
) -> list[dict]:
    serving_config = (
        f"projects/{project_id}"
        f"/locations/{location}"
        f"/collections/default_collection"
        f"/engines/{engine_id}"
        f"/servingConfigs/default_config"
    )
    request = discoveryengine.SearchRequest(
        serving_config=serving_config,
        query=query,
        page_size=page_size,
        filter=filter_ or "",
    )
    response = client.search(request=request, timeout=15)
    return [
        {"id": result.document.id, "data": dict(result.document.struct_data)}
        for result in islice(response, page_size)
    ]


def search_speeches(
    query: str,
    page_size: int = 10,
    filter_: str | None = None,
) -> list[dict]:
    return _search(
        query=query,
        project_id=SEARCH_PROJECT_ID,
        location=SEARCH_LOCATION,
        engine_id=SEARCH_ENGINE_ID,
        page_size=page_size,
        filter_=filter_,
    )


def search_votes(
    query: str,
    page_size: int = 10,
    filter_: str | None = None,
) -> list[dict]:
    """Vertex AI Search의 표결 전용 검색 앱을 조회한다."""
    return _search(
        query=query,
        project_id=SEARCH_PROJECT_ID,
        location=SEARCH_LOCATION,
        engine_id=ACTION_SEARCH_ENGINE_ID,
        page_size=page_size,
        filter_=filter_,
    )
