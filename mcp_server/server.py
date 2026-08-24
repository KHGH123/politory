import os

from fastmcp import FastMCP

from rag.bigquery_client import (
    get_meeting_sources as get_meeting_sources_impl,
    resolve_legislator as resolve_legislator_impl,
    get_utterances as get_utterances_impl,
)
from rag.search_client import (
    search_speeches as search_speeches_impl,
    search_votes as search_votes_impl,
)
from rag.retriever import retrieve_speech_evidence as retrieve_speech_evidence_impl


mcp = FastMCP("mcp")


def _filter_value(value: str) -> str:
    """Discovery Engine filter 문자열에 들어갈 값을 escape한다."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


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
def search_votes(
    query: str,
    member_name: str | None = None,
    legislator_id: str | None = None,
    choice: str | None = None,
    page_size: int = 10,
) -> list[dict]:
    """국회 본회의 표결을 검색하고 공식 회의록 출처와 함께 반환합니다.

    의원을 지정하지 않은 안건 결과 질문에는 안건별 집계 문서를 반환합니다.
    의원명, 의원 ID 또는 표결 선택을 지정하면 의원별 표결 문서를 검색합니다.

    Args:
        query: 의안명 또는 표결 주제.
        member_name: 특정 의원 이름.
        legislator_id: 동명이인을 구분하는 정규화 의원 ID.
        choice: YES(찬성), NO(반대), ABSTAIN(기권) 중 하나.
        page_size: 반환할 최대 결과 수(1~20).
    """
    if not query.strip():
        return []

    normalized_choice = choice.upper() if choice else None
    if normalized_choice and normalized_choice not in {"YES", "NO", "ABSTAIN"}:
        raise ValueError("choice는 YES, NO, ABSTAIN 중 하나여야 합니다.")

    member_scope = bool(member_name or legislator_id or normalized_choice)
    document_type = (
        "assembly_vote_member" if member_scope else "assembly_vote_summary"
    )
    filters = [f'document_type: ANY("{document_type}")']
    if member_name:
        filters.append(f'member_name: ANY("{_filter_value(member_name)}")')
    if legislator_id:
        filters.append(f'legislator_id: ANY("{_filter_value(legislator_id)}")')
    if normalized_choice:
        filters.append(f'choice: ANY("{normalized_choice}")')
    if member_scope:
        filters.append('identity_status: ANY("MATCHED")')

    search_results = search_votes_impl(
        query=query.strip(),
        page_size=max(1, min(page_size, 20)),
        filter_=" AND ".join(filters),
    )
    evidence = []
    for result in search_results:
        data = dict(result.get("data", {}))
        if member_name and data.get("member_name") != member_name:
            continue
        data["document_id"] = result.get("id")
        evidence.append(data)

    meeting_ids = list(
        dict.fromkeys(
            item.get("meeting_id") for item in evidence if item.get("meeting_id")
        )
    )
    meeting_sources = get_meeting_sources_impl(meeting_ids)
    for item in evidence:
        source = meeting_sources.get(item.get("meeting_id"), {})
        item["meeting_title"] = source.get("meeting_title")
        item["official_url"] = source.get("official_url")
        item["source_pdf_url"] = source.get("source_pdf_url")
    return evidence


@mcp.tool()
def get_utterances(
    utterance_ids: list[str],
) -> list[dict]:
    """발언 ID 목록으로 전체 발언 원문과 출처 정보를 조회합니다."""
    return get_utterances_impl(utterance_ids)


@mcp.tool()
def retrieve_speech_evidence(
    query: str,
    legislator_id: str,
    page_size: int = 20,
    min_chars: int = 20,
    max_results: int = 10,
) -> dict:
    """회의록을 검색하고 중복·짧은 발언을 제거한 전체 발언 근거를 반환합니다.

    반환 순서는 Vertex AI Search 결과 순서이며 날짜 정렬은 수행하지 않습니다.
    """
    # 인물 중심 서비스이므로 의원 ID 없는 전체 회의록 검색은 허용하지 않는다.
    # resolve_legislator가 실패했는데 이름만으로 검색하면 비의원 동명이인의
    # 발언을 의원 발언으로 오인할 수 있다.
    if not legislator_id.strip():
        return {
            "query": query,
            "candidate_count": 0,
            "evidence_count": 0,
            "excluded_short_count": 0,
            "utterances": [],
        }

    filter_ = f'legislator_id: ANY("{_filter_value(legislator_id)}")'

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
