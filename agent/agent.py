"""ADK CLI(adk web / adk run)가 찾는 진입점. root_agent 변수가 있어야 인식됨.

TODO(C): instruction에 가드레일 원칙(해석적 판단 금지) 반영, tools 연결.
"""
import os

from google.adk.agents import SequentialAgent
from agent.subagent import router, fetch, summarizer, guardrail
from .model import MODEL_NAME

root_agent = SequentialAgent(
    name="politory_agent",
    # before_model_callback=,
    # after_model_callback=
    sub_agents=[router, fetch, summarizer, guardrail],
)
