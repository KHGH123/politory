from google.adk.agents import Agent
from pydantic import BaseModel

import os

model_name = os.getenv("MODEL")
class RouteDecision(BaseModel):
    api: bool
    rag: bool
    web: bool

router = Agent(
    name = "router",
    model = model_name,
    instruction="""
    사용자 질문을 보고 아래 세 정보 소스 중 무엇이 필요한지 true/false로 판단해라.
    - api: 법안/표결/의원 정보 조회가 필요하면 true
    - rag: 해당 의원의 과거 발언 검색이 필요하면 true
    - web: 최근 뉴스/보도 확인이 필요하면 true
    """,
    output_schema=RouteDecision,
    output_key='route,'
)