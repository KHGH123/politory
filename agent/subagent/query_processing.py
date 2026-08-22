"""Query Processing — 사용자 질문을 분석해 어떤 소스 에이전트가 필요한지 라우팅한다.
(담당: 오케스트레이션)

route 결과는 fetch.py(ParallelAgent)의 skip_if_not_routed 콜백이 읽어서
speech_agent/action_agent/context_agent 실행 여부를 결정한다.
"""
import os

from google.adk.agents import Agent
from google.genai import types
from pydantic import BaseModel


class RouteDecision(BaseModel):
    action: bool  # 법안/표결/의원 정보(action_agent) 조회 필요 여부
    speech: bool  # 과거 발언(speech_agent) 검색 필요 여부
    context: bool  # 뉴스/맥락(context_agent) 확인 필요 여부


query_processing = Agent(
    name="query_processing",
    model=os.getenv("MODEL", "gemini-3.5-flash"),
    # bool 3개만 판단하는 단순 라우팅에 내부 추론(thinking)이 불필요하게
    # 시간을 잡아먹는 걸 실측으로 확인(콜드스타트 제외 매번 ~7초) — 꺼서
    # 응답 속도를 줄인다. 다른 서브에이전트(merge/guardrail 등)는 판단이
    # 더 복잡해서 thinking을 그대로 둔다.
    generate_content_config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    ),
    instruction="""
    사용자 질문을 보고 아래 세 정보 소스 중 무엇이 필요한지 true/false로 판단해라.
    - action: 법안/표결/의원 정보 조회가 필요하면 true
    - speech: 해당 의원의 과거 발언 검색이 필요하면 true
    - context: 최근 뉴스/보도, 정치적 맥락 확인이 필요하면 true
    """,
    output_schema=RouteDecision,
    output_key="route",
)
