from rag.bigquery_client import resolve_legislator
from rag.retriever import retrieve_utterances


def rag_search(
    question: str,
    legislator_name: str | None = None,
    page_size: int = 10,
) -> dict:
    legislator = None
    filter_ = None

    if legislator_name:
        matches = resolve_legislator(legislator_name)

        if not matches:
            return {
                "question": question,
                "error": f"의원 '{legislator_name}'을 찾을 수 없습니다.",
                "utterances": [],
            }

        if len(matches) > 1:
            return {
                "question": question,
                "error": "동명이인 의원이 존재합니다.",
                "candidates": matches,
                "utterances": [],
            }

        legislator = matches[0]
        legislator_id = legislator["legislator_id"]

        filter_ = f'legislator_id: ANY("{legislator_id}")'

    utterances = retrieve_utterances(
        query=question,
        page_size=page_size,
        filter_=filter_,
    )

    return {
        "question": question,
        "legislator": legislator,
        "utterance_count": len(utterances),
        "utterances": utterances,
    }