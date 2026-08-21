"""Source Verification — 각 소스 에이전트(speech/action/context) 출력이 실제 도구
결과에 근거하는지 검증하고, 근거가 불분명하면 재검색을 유도한다. (담당: 오케스트레이션)

evidence_synthesis의 guardrail과는 검사 대상이 다르다:
- guardrail(evidence_synthesis.py): 종합된 답변에 "입장을 바꿨다" 같은 해석적
  판단 문장이 섞였는지 검사 (평가/해석 문제)
- verifier(이 파일): 개별 소스의 진술이 실제 조회 결과에서 온 것인지, 도구 호출
  없이 지어낸(hallucinated) 것인지 검사 (사실 근거/attribution 문제)

각 소스 에이전트를 LoopAgent로 감싸 다음처럼 동작한다:
    speech_verified_loop (LoopAgent, max_iterations=2)
        speech_agent      (다른 팀원 몫) -> speech_info
        speech_verifier   (이 파일)      -> 근거 있으면 exit_loop() 호출해 루프 종료,
                                            불분명하면 retry_hint를 state에 남기고
                                            같은 루프를 한 번 더 돈다.
speech_agent(등)의 instruction은 session state의 retry_hint를 참고해 재검색
전략을 바꿔야 한다 — 이건 소스 에이전트 담당자에게 별도로 안내가 필요하다.

TODO(오케스트레이션): 지금은 LLM 판단 1회 호출로 근거 여부를 검사한다(구조화된 도구
반환값 대조가 아님). speech/action/context_agent의 output_schema가 확정되면
"tool 호출 자체가 없었는지"를 정확히 판별하는 규칙 기반 사전 필터를 앞단에 추가하는
것을 검토.
"""
import os

from google.adk.agents import Agent, LoopAgent
from google.adk.tools import exit_loop

from .sources import speech_agent, action_agent, context_agent

_model = os.getenv("MODEL", "gemini-3.5-flash")

MAX_VERIFICATION_ITERATIONS = 2


def _make_verifier(name: str, info_key: str, retry_hint_key: str) -> Agent:
    """소스 하나의 결과(info_key)를 검증하는 verifier 에이전트를 만든다.

    통과 시 exit_loop()을 호출해 LoopAgent를 즉시 종료한다.
    불통과 시 retry_hint_key에 재검색 지시를 남기고 그냥 끝낸다
    (도구 호출을 안 하므로 LoopAgent가 다음 iteration으로 넘어간다).
    """
    return Agent(
        name=name,
        model=_model,
        tools=[exit_loop],
        instruction=f"""
        아래는 소스 에이전트가 만든 결과다.

        [{info_key}]
        {{{info_key}?}}

        이 내용이 실제 도구(국회 API/벡터DB/뉴스 검색) 호출 결과에 근거하는지 판단하라.
        다음 신호가 보이면 "근거 불분명(hallucination 의심)"으로 판단한다:
        - 구체적 날짜·회의명·인용문이 있는데 실제 조회를 수행했다는 언급이 없음
        - "~일 것으로 보입니다", "~했을 가능성이 있습니다" 같은 추측성 표현으로
          사실을 서술함
        - "정보가 없습니다/조회 결과가 없습니다"처럼 결과 없음을 명시했거나, 위
          [{info_key}]가 완전히 비어 있는 경우는 hallucination이 아니라 정상
          응답이므로 근거 있음으로 판단한다.

        - 근거가 있다고 판단되면: exit_loop 도구를 호출하라. 다른 텍스트는 출력하지 마라.
        - 근거가 불분명하면: exit_loop을 호출하지 말고, 어떤 부분이 불분명한지와
          다음 시도에서 어떤 키워드·조건으로 다시 검색해야 하는지를 한 문단으로
          {retry_hint_key} 값으로 출력하라.
        """,
        output_key=retry_hint_key,
    )


speech_verifier = _make_verifier("speech_verifier", "speech_info", "speech_retry_hint")
action_verifier = _make_verifier("action_verifier", "action_info", "action_retry_hint")
context_verifier = _make_verifier("context_verifier", "context_info", "context_retry_hint")

speech_verified_loop = LoopAgent(
    name="speech_verified_loop",
    description="speech_agent 실행 후 근거를 검증하고, 불분명하면 재검색한다.",
    max_iterations=MAX_VERIFICATION_ITERATIONS,
    sub_agents=[speech_agent, speech_verifier],
)

action_verified_loop = LoopAgent(
    name="action_verified_loop",
    description="action_agent 실행 후 근거를 검증하고, 불분명하면 재검색한다.",
    max_iterations=MAX_VERIFICATION_ITERATIONS,
    sub_agents=[action_agent, action_verifier],
)

context_verified_loop = LoopAgent(
    name="context_verified_loop",
    description="context_agent 실행 후 근거를 검증하고, 불분명하면 재검색한다.",
    max_iterations=MAX_VERIFICATION_ITERATIONS,
    sub_agents=[context_agent, context_verifier],
)
