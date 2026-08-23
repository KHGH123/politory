"""ParallelAgent 오케스트레이션 뼈대. (담당: 오케스트레이션)

개별 소스 에이전트(speech/action/context)의 instruction·tool 로직은
agent/subagent/sources/ 아래 각자 파일로 분리되어 있음 (담당: 다른 팀원).
각 소스 에이전트는 source_verification.py에서 검증 루프(LoopAgent)로 감싸져
있어, 이 파일은 "검증까지 끝난 결과"를 병렬로 모으는 역할만 한다.

이 파일은 query_processing의 route 결과에 따라 어떤 소스를 skip할지만 다룬다.
"""
from google.adk.agents import ParallelAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from .source_verification import (
    speech_verified_loop,
    action_verified_loop,
    context_verified_loop,
)


def skip_if_not_routed(source_key: str):
    """route[source_key]가 False면 에이전트 실행 자체를 건너뛰는 콜백 팩토리"""
    def _callback(callback_context: CallbackContext):
        # session.state["route"] = {"action": false, "speech": true, "context": false}
        route = callback_context.state.get("route", {})
        if not route.get(source_key, False):
            return types.Content(
                parts=[types.Part(text="")],
            )
        return None  # 정상 실행
    return _callback


# LoopAgent(검증 루프) 단위로 skip 콜백을 건다. before_agent_callback이 스킵을
# 반환하면 LoopAgent 전체(내부의 소스 에이전트 + verifier)가 통째로 건너뛰어진다.
speech_verified_loop.before_agent_callback = skip_if_not_routed("speech")
action_verified_loop.before_agent_callback = skip_if_not_routed("action")
context_verified_loop.before_agent_callback = skip_if_not_routed("context")

fetch = ParallelAgent(
    name="multi_info_fetcher",
    sub_agents=[speech_verified_loop, action_verified_loop, context_verified_loop],
    description="""
    speech/action/context 각각을 검증 루프(source_verification.py)와 함께
    동시에 실행해 정보를 병렬 수집
    """,
)
