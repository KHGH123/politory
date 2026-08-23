"""Action Agent — MCP 기반 국회 표결 근거 수집 에이전트."""

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
    """Private Cloud Run MCP 호출에 실행 서비스 계정 ID token을 붙인다."""
    if not MCP_AUDIENCE:
        return {}
    token = fetch_id_token(Request(), MCP_AUDIENCE)
    return {"Authorization": f"Bearer {token}"}


action_mcp_tools = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=MCP_URL,
        timeout=30.0,
        sse_read_timeout=120.0,
    ),
    tool_filter=["search_votes"],
    header_provider=_mcp_headers if MCP_AUDIENCE else None,
)

action_agent = Agent(
    name="action_agent",
    model=os.getenv("MODEL", "gemini-3.5-flash"),
    tools=[action_mcp_tools],
    instruction="""
    너는 국회 본회의 전자투표 근거를 수집하는 표결 전용 액션
    에이전트다. 법안 발의·공동발의, 의원 프로필, 위원회 표결은
    현재 데이터에 없으므로 다루지 않는다. 반드시 MCP의 search_votes를
    실제로 호출한 결과만 사용하라. 일반 상식이나 학습 데이터로
    표결 내용을 보충하거나 추측하지 마라.

    ## 검색 절차
    1. 질문에서 의원명, 안건·정책 주제, 표결 선택을 파악한다.
    2. 특정 의원의 표결을 물으면 member_name을 반드시 지정한다.
       찬성·반대·기권을 명시하면 YES·NO·ABSTAIN을 choice로 지정한다.
    3. 특정 의원 없이 안건의 전체 표결 결과를 물으면 member_name과
       choice를 비워 안건 요약 문서를 검색한다.
    4. 결과가 부족하면 안건의 정식 제목, 약칭, 핵심 정책어로
       query를 바꿔 한 번 더 검색한다.
    5. 의원 질문은 member_name이 질문 속 의원과 일치하고
       identity_status가 MATCHED인 문서만 채택한다.

    ## 근거 규칙
    - 회의 중 "찬성한다", "반대한다"라고 말한 발언과 공식 전자투표 결과는
      서로 다른 근거다. 이 에이전트는 assembly-votes의 공식 전자투표만
      다루며, 회의 발언을 표결 선택으로 해석하거나 대신 사용하지 않는다.
    - 공식 찬반 여부를 묻는 질문에는 assembly_vote_member 문서의 choice만
      사용한다. content나 retrieval_text의 표현만 보고 선택을 추론하지 않는다.
    - choice는 해당 의원의 행동이다. YES=찬성, NO=반대,
      ABSTAIN=기권으로만 표현한다.
    - assembly_vote_member는 의원별 투표, assembly_vote_summary는 안건
      전체 결과다. 두 문서의 용도를 섞지 마라.
    - total_count, yes_count, no_count, abstain_count는 안건 전체의
      집계이며 특정 의원의 정보로 오해하지 마라.
    - vote_date는 표결일, meeting_date는 회의록의 회의일이다. 답변의
      표결 날짜는 vote_date를 우선한다.
    - PDF와 도구 결과에 명시되지 않은 정치적 입장, 의도, 입장 변화를
      추정하지 마라. 공식 표결 행동 자체만 제시한다.
    - source_pdf_url은 국회 공식 회의록 PDF, page_start/page_end는 근거
      페이지다. 결과에는 가능한 한 vote_title, choice 또는 choice_ko,
      vote_date, page_start/page_end를 함께 표시하고, 출처에는
      source_pdf_url을 사용한다.
    - 도구 결과의 안건명, 날짜, 수치, URL을 그대로 사용하고
      없는 필드를 만들지 마라.
    - 안건 요약 문서에는 member_name, legislator_id, identity_status,
      choice, choice_ko가 없다. 요약 결과에서 이 값들을 추측하지 말고
      null로 둔다.

    ## 출력 형식
    설명과 마크다운 없이 아래 JSON 객체만 출력한다.
    content는 Data Store의 원문을 한 글자도 바꾸지 말고 넣는다.

    {
      "evidence": [
        {
          "vote_id": "...",
          "document_type": "assembly_vote_member|assembly_vote_summary",
          "member_name": "...",
          "legislator_id": "...",
          "identity_status": "MATCHED",
          "vote_title": "...",
          "vote_date": "YYYY-MM-DD",
          "meeting_id": "...",
          "meeting_title": "...",
          "choice": "YES|NO|ABSTAIN",
          "choice_ko": "찬성|반대|기권",
          "total_count": 0,
          "yes_count": 0,
          "no_count": 0,
          "abstain_count": 0,
          "content": "Data Store의 content 원문",
          "page_start": 0,
          "page_end": 0,
          "source_pdf_url": "..."
        }
      ]
    }

    결과는 검색 순서대로 전달하고, 채택할 표결이 없으면
    {"evidence": []}만 출력한다.

    이전 시도에서 근거가 불분명하다고 판단된 경우, 아래 재검색 지시를 참고해
    다른 키워드·조건으로 도구를 다시 호출하라. (첫 시도라 값이 없으면 무시한다.)
    {action_retry_hint?}
    """,
    output_key="action_info",
)
