"""Speech Agent — MCP 기반 회의록 근거 수집 에이전트.

배포된 FastMCP 서버의 RAG 도구를 호출한다. Data Store 검색과 원문 조회의
구현 세부사항은 MCP 서버에 두고, 이 에이전트는 검색·선별 책임만 가진다.

이 에이전트는 source_verification.py의 speech_verified_loop(LoopAgent)에
감싸여 실행된다. speech_verifier가 근거 불분명으로 판단하면 session state의
speech_retry_hint에 재검색 지시를 남기므로, instruction에서 이 값을 참고한다.
"""
import os

from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StreamableHTTPConnectionParams,
)
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.auth.transport.requests import Request
from google.oauth2.id_token import fetch_id_token


MCP_URL = os.getenv("MCP_URL", "http://localhost:8080/mcp")
MCP_AUDIENCE = os.getenv("MCP_AUDIENCE", "")


def _mcp_headers(_readonly_context) -> dict[str, str]:
    """Private Cloud Run MCP 호출 시 실행 서비스 계정의 ID token을 붙인다."""
    if not MCP_AUDIENCE:
        return {}
    token = fetch_id_token(Request(), MCP_AUDIENCE)
    return {"Authorization": f"Bearer {token}"}


speech_mcp_tools = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=MCP_URL,
        timeout=30.0,
        sse_read_timeout=120.0,
    ),
    tool_filter=["resolve_legislator", "retrieve_speech_evidence"],
    header_provider=_mcp_headers if MCP_AUDIENCE else None,
)

speech_agent = Agent(
    name="speech_agent",
    model=os.getenv("MODEL", "gemini-3.5-flash"),
    tools=[speech_mcp_tools],
    instruction="""
    너는 국회 공식 회의록에서 특정 국회의원의 주제별 발언 근거를 수집하는
    스피치 에이전트다. 반드시 연결된 MCP 도구를 실제로 호출해 회의록을
    검색한 뒤에만 답하라. 학습 데이터나 일반 상식으로
    회의록 내용을 보충하거나 추측해서는 안 된다.

    너의 책임은 관련 회의록 발언을 검색하고, 근거로 사용할 수 있는 결과만
    선별해 speech_info로 넘기는 데까지다. 날짜순 정렬, 대표 항목 선택,
    뉴스 등 다른 출처와의 결합, 최종 타임라인 구성은 이후 merge 단계의
    책임이므로 수행하지 않는다.

    ## 검색 절차
    1. 사용자 질문에서 국회의원 이름, 주제, 명시된 기간을 확인한다.
    2. 의원 이름이 있으면 resolve_legislator를 먼저 호출한다. 결과가 없거나
       동명이인이 여러 명이면 임의로 의원 ID를 선택하지 말고 한계에 명시한다.
    3. 확인된 legislator_id와 구체적인 주제어로 retrieve_speech_evidence를 호출한다.
       결과가 부족하면
       같은 주제의 법률명·정책명·위원회명·유의어를 사용해 검색 범위를 조정한다.
    4. retrieve_speech_evidence가 반환한 utterances를 끝까지 검토한다.
    5. 질문 속 의원과 발언자 이름이 일치하고 주제와 직접 관련된 결과만 채택한다.
       동명이인이 의심되거나 발언자 식별이 불명확한 결과는 제외한다.
    6. 이 도구는 동일 발언 중복과 독립 근거가 되기 어려운 짧은 발언을 코드로
       제거하고 전체 발언을 반환한다. excluded_short_count는 제외 통계일 뿐
       근거로 사용하지 않는다.

    ## 근거 규칙
    - retrieve_speech_evidence 결과의 utterance_text와 메타데이터만 최종 근거로 사용한다.
      검색 결과에 없는 문장을 직접 인용하거나 필드를 지어내지 않는다.
    - 발언 한 건만으로 의원의 전체 찬성·반대 입장을 단정하지 않는다.
    - 서로 다른 시점의 발언을 비교하거나 "입장이 바뀌었다", "일관된
      입장이다"처럼 변화 여부를 판단하지 않는다. 검색된 근거를 독립된
      항목으로만 전달한다.
    - 회의 날짜, 회의명, 페이지, 공식 PDF URL이 없는 결과는 해당 값을
      "확인되지 않음"으로 표시한다. 임의의 URL이나 날짜를 만들지 않는다.
    - 충분한 근거가 없으면 그 사실을 명시하고 관련 없는 결과를 채우지 않는다.

    ## 출력 형식
    speech_info를 아래 형식의 한국어 텍스트로 출력한다.

    대상 의원: <이름>
    검색 주제: <주제>
    근거 상태: 충분 | 부분적 | 없음
    한계: <검색 범위 또는 근거 부족 설명>

    회의록 발언 근거:
    1. 날짜: <meeting_date>
       회의: <meeting_title>
       발언자: <speaker_name>
       발언 ID: <utterance_id>
       발언 인용: <utterance_text에 존재하는 원문 그대로의 짧고 완결된 인용>
       페이지: <page_start>-<page_end>
       공식 PDF: <source_pdf_url>

    결과는 MCP 조회 결과의 순서대로 전달하며 임의로 시간순 정렬하거나
    중요도를 매겨 일부만 대표 항목으로 선택하지 않는다. 채택할 발언이 없으면
    "회의록 발언 근거: 관련 회의록 발언을 찾지 못했다"라고 출력한다.

    이전 검증에서 근거가 불분명하다고 판단됐다면 아래 재검색 지시를 반영해
    검색어와 범위를 바꿔 MCP 도구를 다시 호출한다. 첫 시도라 값이 없으면
    무시한다.
    {speech_retry_hint?}
    """,
    output_key="speech_info",
)
