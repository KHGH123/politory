"""뉴스/보도 검색 툴 (2차 출처). NAVER API HUB 뉴스 검색 API를 호출한다.

인증: config.settings의 NAVER_CLIENT_ID/NAVER_CLIENT_SECRET
(.env의 X-NCP-APIGW-API-KEY-ID / X-NCP-APIGW-API-KEY 값).
엔드포인트: https://naverapihub.apigw.ntruss.com/search/v1/news
문서: https://api.ncloud-docs.com/docs/naver-api-hub-search-news
일 호출 한도 25,000회.

mcp_server/로 노출 예정 — 지금은 ADK Agent가 바로 호출할 수 있는 일반 함수로
구현해 tools=[search_news]에 직접 연결한다.
"""
import httpx

from config import settings

NEWS_SEARCH_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"


def search_news(query: str, display: int = 10) -> list[dict]:
    """네이버 뉴스 검색 API로 관련 기사를 찾는다.

    Args:
        query: 검색어 (예: 의원 이름, 정책 키워드).
        display: 반환할 기사 수 (1~100, 기본 10). 너무 작으면(예: 5) sort=sim이라도
            결과 상위가 최신 기사 위주로 쏠리는 경향이 있다 — 검색어가 시사성
            이슈일 때 total이 수천~수만 건인 경우 5개로는 최근 며칠치만 잡히는
            걸 실제로 확인해서 늘림.

    Returns:
        기사 목록. 각 항목은 {"title", "description", "url", "published_at"}.
        title/description의 <b> 강조 태그와 HTML 엔티티는 제거해서 반환한다.
        API 키가 없거나 호출이 실패하면 빈 리스트를 반환한다(예외를 던지지 않음 —
        source_verification.py의 verifier가 "정보 없음"으로 정상 처리하게 하기 위함).
    """
    if not settings.NAVER_CLIENT_ID or not settings.NAVER_CLIENT_SECRET:
        return []

    headers = {
        "X-NCP-APIGW-API-KEY-ID": settings.NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": settings.NAVER_CLIENT_SECRET,
    }
    params = {
        "query": query,
        "display": display,
        # sort="date"(최신순)로 두면 오늘 기사만 계속 잡혀서 과거 발언·정책 변화를
        # 추적하는 이 서비스 취지(CLAUDE.md F-02: 발언 타임라인)와 안 맞았다
        # (실제로 "최신 뉴스만 나온다"는 문제로 확인). sim(정확도순)으로 바꿔
        # 검색어와 관련성 높은 기사를 시점 무관하게 가져오게 한다.
        "sort": "sim",
    }

    try:
        resp = httpx.get(NEWS_SEARCH_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
    except httpx.HTTPError:
        return []

    items = resp.json().get("items", [])
    return [
        {
            "title": _strip_tags(item.get("title", "")),
            "description": _strip_tags(item.get("description", "")),
            "url": item.get("originallink") or item.get("link"),
            "published_at": item.get("pubDate"),
        }
        for item in items
    ]


def _strip_tags(text: str) -> str:
    """네이버 검색 API가 검색어 강조에 쓰는 <b> 태그와 HTML 엔티티를 제거한다."""
    import html
    import re

    return html.unescape(re.sub(r"</?b>", "", text))
