import os

from fastmcp import FastMCP

from rag.bigquery_client import (
    resolve_legislator as resolve_legislator_impl,
    get_utterances as get_utterances_impl,
)
from rag.search_client import search_speeches as search_speeches_impl
from rag.retriever import retrieve_speech_evidence as retrieve_speech_evidence_impl


mcp = FastMCP("mcp")


@mcp.tool()
def resolve_legislator(name: str) -> list[dict]:
    """의원 이름으로 의원 ID와 기본 정보를 조회합니다."""
    return resolve_legislator_impl(name)


@mcp.tool()
def search_speeches(
    query: str,
    legislator_id: str | None = None,
    page_size: int = 10,
) -> list[dict]:
    """
    Vertex AI Search에서 국회 발언을 검색합니다.
    legislator_id가 있으면 해당 의원의 발언으로 제한합니다.
    """

    filter_ = None

    if legislator_id:
        filter_ = f'legislator_id: ANY("{legislator_id}")'

    return search_speeches_impl(
        query=query,
        page_size=page_size,
        filter_=filter_,
    )


@mcp.tool()
def get_utterances(
    utterance_ids: list[str],
) -> list[dict]:
    """발언 ID 목록으로 전체 발언 원문과 출처 정보를 조회합니다."""
    return get_utterances_impl(utterance_ids)


@mcp.tool()
def retrieve_speech_evidence(
    query: str,
    legislator_id: str | None = None,
    page_size: int = 20,
    min_chars: int = 20,
    max_results: int = 10,
) -> dict:
    """회의록을 검색하고 중복·짧은 발언을 제거한 전체 발언 근거를 반환합니다.

    반환 순서는 Vertex AI Search 결과 순서이며 날짜 정렬은 수행하지 않습니다.
    """
    filter_ = None
    if legislator_id:
        filter_ = f'legislator_id: ANY("{legislator_id}")'

    return retrieve_speech_evidence_impl(
        query=query,
        page_size=page_size,
        filter_=filter_,
        min_chars=min_chars,
        max_results=max_results,
    )


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
    )
