from rag.search_client import search_speeches
from rag.bigquery_client import get_utterances


MIN_EVIDENCE_CHARS = 20


def _normalized_text_length(text: str) -> int:
    """공백을 제외한 실제 발언 글자 수를 센다."""
    return len("".join(text.split()))


def select_evidence_utterances(
    utterances: list[dict],
    min_chars: int = MIN_EVIDENCE_CHARS,
    max_results: int | None = None,
) -> tuple[list[dict], int]:
    """독립된 최종 근거로 쓰기 어려운 짧은 발언을 결정적으로 제외한다.

    검색 순서를 유지하며 날짜 정렬이나 중요도 재정렬은 하지 않는다. 동일 ID는
    방어적으로 한 번 더 제거한다. 반환값의 두 번째 항목은 제외된 짧은 발언 수다.
    """
    selected: list[dict] = []
    seen: set[str] = set()
    excluded_short_count = 0

    for utterance in utterances:
        utterance_id = utterance.get("utterance_id")
        text = utterance.get("utterance_text") or ""

        if not utterance_id or utterance_id in seen:
            continue
        seen.add(utterance_id)

        if _normalized_text_length(text) < min_chars:
            excluded_short_count += 1
            continue

        selected.append(utterance)
        if max_results is not None and len(selected) >= max_results:
            break

    return selected, excluded_short_count


def retrieve_utterances(
    query: str,
    page_size: int = 10,
    filter_: str | None = None,
) -> list[dict]:
    search_results = search_speeches(
        query=query,
        page_size=page_size,
        filter_=filter_,
    )

    utterance_ids: list[str] = []
    seen: set[str] = set()

    for result in search_results:
        data = result.get("data", {})
        utterance_id = data.get("primary_utterance_id")

        if not utterance_id:
            continue

        if utterance_id in seen:
            continue

        seen.add(utterance_id)
        utterance_ids.append(utterance_id)

    if not utterance_ids:
        return []

    return get_utterances(utterance_ids)


def retrieve_speech_evidence(
    query: str,
    page_size: int = 20,
    filter_: str | None = None,
    min_chars: int = MIN_EVIDENCE_CHARS,
    max_results: int = 10,
) -> dict:
    """검색·중복 제거·전체 발언 조회·짧은 발언 제외를 한 번에 수행한다."""
    utterances = retrieve_utterances(
        query=query,
        page_size=page_size,
        filter_=filter_,
    )
    selected, excluded_short_count = select_evidence_utterances(
        utterances,
        min_chars=min_chars,
        max_results=max_results,
    )
    return {
        "query": query,
        "candidate_count": len(utterances),
        "evidence_count": len(selected),
        "excluded_short_count": excluded_short_count,
        "utterances": selected,
    }
