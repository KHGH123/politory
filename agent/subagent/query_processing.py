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
    # bool 3개만 뽑는 라우팅이라 다른 서브에이전트와 다른 전용 env로
    # lite 모델을 쓴다(QUERY_PROCESSING_MODEL, 없으면 gemini-3.5-flash-lite로
    # 폴백 — 공용 MODEL 값을 그대로 물려받지 않음). gemini-3.5-flash-lite는
    # Vertex AI에서 실제 호출 가능함을 확인했다(로컬 스모크 테스트). 이미
    # thinking_budget=0으로 최적화된 지점이라, 모델 자체를 경량화해도
    # "bool 3개 분류"라는 작업 성격상 정확도 저하 위험이 다른 서브에이전트보다
    # 낮다고 판단해 여기서만 먼저 시도한다 — merge/guardrail/verifier/
    # context_agent처럼 판단이 복잡한 곳은 손대지 않는다.
    model=os.getenv("QUERY_PROCESSING_MODEL", "gemini-3.5-flash-lite"),
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

    context는 "최근 뉴스가 명시적으로 언급된 질문"에서만 true로 판단하지
    마라. 위원회 활동, 정책 추진, 법안 처리 같은 질문도 실제로는 그 활동에
    대한 언론 반응·평가·후속 보도가 함께 있어야 맥락이 온전해진다(실측:
    "사법제도 개선", "위원회 활동" 같은 질문에서 뉴스 없이 법안/발언만
    조회되면 그 활동이 실제로 어떻게 진행·평가됐는지가 빠진다). action이나
    speech가 true인 질문은 특별히 뉴스를 배제할 이유(예: 단순 사실 조회,
    "찬성/반대 여부만 알려줘"처럼 표결 결과 하나만 묻는 질문)가 없는 한
    context도 함께 true로 판단해라.
    """,
    output_schema=RouteDecision,
    output_key="route",
)
