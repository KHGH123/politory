"""Speech Agent — 벡터DB(발언 원문) 검색 에이전트.

담당: 다른 팀원(speech/action/context agent 담당).
tools=[]를 agent/tools/vector_search_tool.py의 search_speeches(mcp_server/ 경유)로 채울 것.

이 에이전트는 source_verification.py의 speech_verified_loop(LoopAgent)에
감싸여 실행된다. speech_verifier가 근거 불분명으로 판단하면 session state의
speech_retry_hint에 재검색 지시를 남기므로, instruction에서 이 값을 참고해야 한다.
"""
import os

from google.adk.agents import Agent

speech_agent = Agent(
    name="speech_agent",
    model=os.getenv("MODEL", "gemini-3.5-flash"),
    tools=[],  # TODO: search_speeches
    instruction="""
    벡터DB에서 관련 발언을 검색한다.

    이전 시도에서 근거가 불분명하다고 판단된 경우, 아래 재검색 지시를 참고해
    다른 키워드·조건으로 다시 검색하라. (첫 시도라 값이 없으면 무시한다.)
    {speech_retry_hint?}
    """,
    output_key="speech_info",
)
