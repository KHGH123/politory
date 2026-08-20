"""summarizer가 만든 요약을 정규식으로 검사해서 통과/대체시키는 순수 로직 노드.

LLM한테 "네 답변 문제없는지 스스로 판단해" 시키는 건 신뢰하기 어려워서,
결정론적 정규식 체크로 판단한다. model이 필요 없는 로직이라 LlmAgent를
콜백으로 우회하지 않고 BaseAgent를 직접 상속한다.

TODO: 지금은 위반 시 바로 fallback으로 대체. 스펙대로 "1회 재생성" 하려면
critic/reviser 루프(LoopAgent + exit_loop) 패턴으로 확장 필요.
"""
import re
from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types

INTERPRETIVE_PATTERNS = [
    r"입장이?\s*바뀌었",
    r"말을?\s*바꿨",
    r"모순된?다",
    r"이율배반",
    r"번복(했|한|되)",
    r"앞뒤가?\s*다르",
    r"일관성이?\s*없",
]

INJECTION_PATTERNS = [
    r"(이전|위)\s*(지시|명령|프롬프트).*(무시|잊)",
    r"ignore (all )?previous instructions",
    r"system\s*prompt",
]

_interpretive = [re.compile(p) for p in INTERPRETIVE_PATTERNS]
_injection = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

FALLBACK_MESSAGE = "죄송합니다. 안전한 형태로 답변을 재구성하지 못했습니다. 원문 자료를 직접 확인해주세요."


def is_violation(text: str) -> bool:
    return any(p.search(text) for p in _interpretive) or any(p.search(text) for p in _injection)


class GuardrailAgent(BaseAgent):
    """model 없이 정규식 체크만 하는 커스텀 에이전트."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        summary = ctx.session.state.get("summary", "")
        text = FALLBACK_MESSAGE if is_violation(summary) else summary
        yield Event(
            author=self.name,
            content=types.Content(parts=[types.Part(text=text)]),
        )


guardrail = GuardrailAgent(name="guardrail")
