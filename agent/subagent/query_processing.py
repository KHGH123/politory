"""Query Processing — 사용자 질문을 분석해 어떤 소스 에이전트가 필요한지 라우팅한다.
(담당: 오케스트레이션)

route 결과는 fetch.py(ParallelAgent)의 skip_if_not_routed 콜백이 읽어서
speech_agent/action_agent/context_agent 실행 여부를 결정한다.
"""
import os

from google.adk.agents import Agent
from pydantic import BaseModel


class RouteDecision(BaseModel):
    action: bool  # 법안/표결/의원 정보(action_agent) 조회 필요 여부
    speech: bool  # 과거 발언(speech_agent) 검색 필요 여부
    context: bool  # 뉴스/맥락(context_agent) 확인 필요 여부


query_processing = Agent(
    name="query_processing",
    model=os.getenv("MODEL", "gemini-3.5-flash"),
    instruction="""
    사용자 질문을 보고 아래 세 정보 소스 중 무엇이 필요한지 true/false로 판단해라.
    - action: 법안/표결/의원 정보 조회가 필요하면 true
    - speech: 해당 의원의 과거 발언 검색이 필요하면 true
    - context: 최근 뉴스/보도, 정치적 맥락 확인이 필요하면 true
    """,
    output_schema=RouteDecision,
    output_key="route",
)
