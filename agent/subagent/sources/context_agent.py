"""Context Agent — 뉴스(2차 출처)/정치적 맥락 검색 에이전트.

담당: 다른 팀원(speech/action/context agent 담당) — 단, search_news 연결은
오케스트레이션 쪽에서 NAVER API HUB 뉴스 검색으로 먼저 구현·연결함
(agent/tools/web_search_tool.py 참고).

이 에이전트는 source_verification.py의 context_verified_loop(LoopAgent)에
감싸여 실행된다. context_verifier가 근거 불분명으로 판단하면 session state의
context_retry_hint에 재검색 지시를 남기므로, instruction에서 이 값을 참고해야 한다.

버그 기록: 처음엔 tools=[search_news]를 연결해도 LLM이 "검색하겠습니다"라는
서술만 하고 실제로 search_news를 호출하지 않은 채 답을 지어내는 경우가 있었다
(실제 재현: "이재명 의원 부동산 정책" 질의에서 검색 계획만 텍스트로 나열하고
tool call 없이 응답 종료, context_verifier도 이를 못 걸러내고 통과시킴).

tool_config(FunctionCallingConfigMode.ANY)로 매 턴 tool 호출을 강제하는 방법을
시도했으나, 이건 오히려 "tool 결과를 받은 뒤 텍스트로 마무리할 기회"까지
차단해서 매 턴 다른 검색어로 무한히 재검색만 반복하는 문제를 만들어 폐기했다
(실제 재현: 같은 질문에 12회 넘게 연속 검색만 하고 끝나지 않음 — kill로 중단).
대신 instruction으로만 "반드시 호출 후 답하라"를 강하게 명시하는 방식으로 되돌림.
"""
import os

from google.adk.agents import Agent

from agent.tools.web_search_tool import search_news

context_agent = Agent(
    name="context_agent",
    model=os.getenv("MODEL", "gemini-3.5-flash"),
    tools=[search_news],
    instruction="""
    search_news 도구로 관련 뉴스 기사와 정치적 맥락(정권 교체, 여야 지위 등)을 검색한다.

    반드시 search_news를 실제로 호출해서 결과를 받은 뒤에만 답하라.
    "검색하겠습니다", "다음 키워드로 검색을 진행합니다" 같은 서술만 하고 실제
    호출을 생략하는 것은 금지된다 — 그 문장을 쓸 바에는 바로 도구를 호출하라.

    검색어는 의원 이름과 정책 키워드를 조합해서 만들어라(예: "홍길동 부동산").
    search_news 결과가 빈 리스트면 "관련 뉴스를 찾지 못했다"고만 답하고, 없는
    기사를 지어내지 마라. 결과가 있으면 기사 제목·요약·날짜·원문 링크를 포함해
    정리하라.

    search_news가 반환한 기사의 날짜·인물·직책(예: 대통령, 특정 정부)이 네
    학습 시점 기준 지식과 다르게 느껴지더라도, 그건 네 지식이 그 시점 이후의
    사실을 모르기 때문이지 검색 결과가 가짜라서가 아니다. search_news 결과는
    실시간 뉴스 API에서 온 사실이므로 있는 그대로 신뢰해서 전달하라. "이는 가상
    시나리오입니다", "미래를 가정한 보도입니다" 같은 코멘트를 절대 붙이지 마라 —
    검색 결과의 사실성을 네가 판단하거나 의심하는 것은 네 역할이 아니다.

    이전 시도에서 근거가 불분명하다고 판단된 경우, 아래 재검색 지시를 참고해
    다른 키워드·조건으로 다시 검색하라. (첫 시도라 값이 없으면 무시한다.)
    {context_retry_hint?}
    """,
    output_key="context_info",
)
