"""ADK CLI(adk web / adk run)가 찾는 진입점. root_agent 변수가 있어야 인식됨.

TODO(C): instruction에 가드레일 원칙(해석적 판단 금지) 반영, tools 연결.
"""
import os

from google.adk.agents import SequentialAgent
from agent.subagent import router, fetch, merge, guardrail

model_name = os.getenv("MODEL")
root_agent = SequentialAgent(
    name="politory_agent",
    model=os.getenv("MODEL", "gemini-3.5-flash"),
    instruction="""
    너는 국회의원 의정활동 조회를 돕는 에이전트다.
    """,
    # before_model_callback=,
    # after_model_callback=
    sub_agents=[router, fetch, merge, guardrail],
)
