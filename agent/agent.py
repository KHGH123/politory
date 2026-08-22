"""ADK CLI(adk web / adk run)가 찾는 진입점. root_agent 변수가 있어야 인식됨.

파이프라인: query_processing(라우팅) -> fetch(speech/action/context 병렬 조회)
-> evidence_synthesis(근거 종합 + 해석적 판단 검사)

SequentialAgent는 sub_agents를 순서대로 실행하는 순수 오케스트레이터라
model/instruction 파라미터를 받지 않는다 (해당 파라미터는 LlmAgent 전용).
개별 단계의 지시문은 각 서브에이전트 안에 있다.

A2A로 노출할 때는(backend가 A2A 클라이언트로 호출할 계획이면):
    uvicorn agent.agent:a2a_app --host localhost --port 8001
AgentCard는 http://localhost:8001/.well-known/agent-card.json 에서 확인.

TODO: speech_agent/action_agent/context_agent의 tools 연결은 다른 팀원 담당
(agent/subagent/sources/).
"""
import os

from dotenv import load_dotenv

load_dotenv()

from google.adk.a2a.utils.agent_to_a2a import to_a2a
from google.adk.agents import SequentialAgent
from agent.subagent import query_processing, fetch, evidence_synthesis

root_agent = SequentialAgent(
    name="politory_agent",
    description="국회의원 의정활동(발언·법안·표결·뉴스)을 조회해 시간순 근거와 함께 답하는 에이전트.",
    sub_agents=[query_processing, fetch, evidence_synthesis],
)

# adk web/adk run은 root_agent를, `uvicorn agent.agent:a2a_app`은 이걸 찾는다.
a2a_app = to_a2a(
    root_agent,
    host=os.getenv("A2A_HOST", "localhost"),
    port=int(os.getenv("A2A_PORT", "8001")),
)
