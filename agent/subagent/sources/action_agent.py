"""Action Agent — 국회 API(법안/표결/의원정보) 검색 에이전트.

담당: 다른 팀원(speech/action/context agent 담당).
tools=[]를 agent/tools/assembly_api_tool.py의 함수들(mcp_server/ 경유)로 채울 것.

이 에이전트는 source_verification.py의 action_verified_loop(LoopAgent)에
감싸여 실행된다. action_verifier가 근거 불분명으로 판단하면 session state의
action_retry_hint에 재검색 지시를 남기므로, instruction에서 이 값을 참고해야 한다.
"""
import os

from google.adk.agents import Agent

action_agent = Agent(
    name="action_agent",
    model=os.getenv("MODEL", "gemini-3.5-flash"),
    tools=[],  # TODO: get_bills, get_votes, get_member_info
    instruction="""
    국회 API로 법안/표결/의원정보를 조회한다.

    이전 시도에서 근거가 불분명하다고 판단된 경우, 아래 재검색 지시를 참고해
    다른 키워드·조건으로 다시 검색하라. (첫 시도라 값이 없으면 무시한다.)
    {action_retry_hint?}
    """,
    output_key="action_info",
)
