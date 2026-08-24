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

URL 대조 검증(context_verifier 전용): context_agent가 호출한 search_news의
실제 반환 URL 목록을 after_tool_callback으로 session state("context_search_urls")에
기록해두고, context_verifier의 before_agent_callback이 context_info에서 URL을
정규식으로 추출해 이 목록과 대조한다. 목록에 없는 URL이 하나라도 있으면 LLM
호출 전에 즉시 불통과 처리(retry_hint 작성)하고 exit_loop 콜백도 건너뛴다 —
"그럴듯해 보이는 가짜 URL"은 LLM 판단보다 문자열 대조가 훨씬 확실하기 때문이다.
speech는 발언 ID·인용·메타데이터를 MCP 전체 발언과 대조하고, action은
document_id·choice·집계·날짜·본문·PDF 위치를 MCP 표결 문서와 대조한다.
두 검증 모두 도구 호출 결과가 없거나 필드가 달라지면 LLM verifier 호출 전에
불통과 처리하고, 마지막 실패 시 미검증 info를 빈 evidence로 교체한다.
"""
import os
import re

from google.adk.agents import Agent, LoopAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import exit_loop
from google.genai import types

from .sources import speech_agent, action_agent, context_agent
from .sources.action_evidence_validation import validate_action_info
from .sources.speech_evidence_validation import validate_speech_info

_model = os.getenv("MODEL", "gemini-3.5-flash")

MAX_VERIFICATION_ITERATIONS = 2

# 실측 재검토: verifier(speech/action/context) 3개에 thinking_budget=0을
# 적용했더니 merge 단계의 각주-출처 정합 오류 재현율이 3회 중 1회에서
# 3회 중 3회로 오히려 악화되는 게 배포 환경에서 관찰됐다. merge/guardrail
# 자체는 손대지 않았는데 이런 변화가 났다는 건, verifier가 exit_loop
# 여부를 덜 신중하게(추론 없이) 판단해 재검색으로 정제됐어야 할
# speech_info/context_info가 더 뒤섞인 채로 merge에 들어갔을 가능성을
# 시사한다 — merge가 다뤄야 할 소스가 복잡해질수록 각주 배정 실수가
# 늘어나는 것으로 보인다. 이 가설이 확정된 건 아니지만, 확인 전까지는
# verifier의 thinking을 원래대로 되돌린다(exit_loop 판단은 단순 분류
# 처럼 보여도 "어디까지가 근거 있음인가"의 경계 판단에는 추론이
# 기여했을 수 있다는 뜻 — merge/guardrail의 스키마 채우기 작업과
# 성격이 다르다).
_URL_PATTERN = re.compile(r"https?://[^\s\)\]\"'>]+")


def _record_search_news_urls(tool, args, tool_context, tool_response):
    """context_agent의 search_news 호출 결과 URL을 session state에 기록한다.

    context_verifier가 나중에 context_info에 인용된 URL이 실제로 이 목록에
    있는지 대조하는 데 쓴다. after_tool_callback은 반환값이 None이면 원래
    tool_response를 그대로 쓰므로, 여기서는 부작용(state 기록)만 하고 None을
    반환한다.

    ADK가 이 콜백을 키워드 인자(tool=, args=, tool_context=, tool_response=)로
    호출하므로 파라미터명을 그대로 맞춰야 한다 — 실제로 이름을 바꿨다가
    TypeError로 런타임에 깨지는 걸 확인하고 원복함. args 자체는 안 쓴다.
    """
    del args
    if tool.name != "search_news":
        return None
    # search_news(agent/tools/web_search_tool.py)는 list[dict]를 직접 반환한다
    # (딕셔너리로 감싸지 않음). tool_response.get(...)으로 접근하려다
    # AttributeError('list' object has no attribute 'get')로 실제로 깨진 걸
    # adk web 수동 테스트에서 확인하고 고쳤다 — InMemoryRunner로 event의
    # function_response.response만 봤을 때는 ADK가 감싼 형태(dict)라 착각했음.
    urls = set(tool_context.state.get("context_search_urls", []))
    for item in tool_response or []:
        url = item.get("url")
        if url:
            urls.add(url)
    tool_context.state["context_search_urls"] = list(urls)
    return None


def _make_url_check_callback(known_urls_key: str, info_key: str, retry_hint_key: str):
    """context_verifier 전용 before_agent_callback.

    context_info에서 URL을 뽑아 known_urls_key(session state)에 없는 URL이
    있으면 LLM을 부르지 않고 바로 불통과 처리한다. 문제 없으면 None을 반환해
    평소대로 LLM 검증(instruction)으로 넘어간다.

    retry_hint_key는 함수 본문에서 직접 쓰이진 않지만, 이 콜백이 반환하는
    Content의 텍스트가 Agent의 output_key(retry_hint_key)로 자동 저장되는
    ADK 규칙을 호출부에서 명확히 알아볼 수 있도록 시그니처에 남겨둔다.
    """
    def _callback(callback_context: CallbackContext):
        info_text = callback_context.state.get(info_key, "") or ""
        cited_urls = set(_URL_PATTERN.findall(info_text))
        if not cited_urls:
            return None  # URL 인용이 없으면 이 검사는 건너뛰고 LLM 판단에 맡긴다.

        known_urls = set(callback_context.state.get(known_urls_key, []))
        fake_urls = cited_urls - known_urls
        if not fake_urls:
            return None  # 인용된 URL이 전부 실제 검색 결과에 있음 — 정상 통과.

        # before_agent_callback이 Content를 반환하면 ADK가 그 텍스트를 그대로
        # output_key(retry_hint_key)에 저장한다 — 별도로 state[...]에 쓰면
        # 이 반환값이 덮어써버리므로, 힌트 메시지는 반드시 텍스트 안에 담아야 한다.
        hint = (
            "이전 답변에 실제 검색 결과에 없는 URL이 포함되어 있었다"
            f"({', '.join(sorted(fake_urls))}). search_news를 다시 호출해서"
            " 그 결과에 실제로 포함된 url만 인용하라. url을 지어내지 마라."
        )
        # exit_loop을 호출하지 않는 응답 -> LoopAgent가 다음 iteration으로 넘어간다.
        return types.Content(parts=[types.Part(text=hint)])

    return _callback


def _check_speech_against_mcp(callback_context: CallbackContext):
    """speech_info의 인용·메타데이터를 실제 MCP 원문과 문자열로 대조한다."""
    valid, hint = validate_speech_info(
        callback_context.state.get("speech_info"),
        callback_context.state.get("speech_source_utterances", {}),
    )
    if valid:
        return None
    # 마지막 재시도까지 실패해 LoopAgent가 종료되더라도 미검증 내용이
    # merge 단계로 넘어가지 않게 즉시 안전한 빈 결과로 교체한다.
    callback_context.state["speech_info"] = '{"evidence": []}'
    return types.Content(parts=[types.Part(text=hint)])


def _check_action_against_mcp(callback_context: CallbackContext):
    """action_info의 표결 필드를 실제 MCP search_votes 결과와 대조한다."""
    valid, hint = validate_action_info(
        callback_context.state.get("action_info"),
        callback_context.state.get("action_source_votes", {}),
    )
    if valid:
        return None
    # 마지막 재시도까지 실패해도 미검증 표결이 merge로 넘어가지 않게 한다.
    callback_context.state["action_info"] = '{"evidence": []}'
    return types.Content(parts=[types.Part(text=hint)])


def _make_verifier(
    name: str, info_key: str, retry_hint_key: str, check_own_actions_only: bool = False
) -> Agent:
    """소스 하나의 결과(info_key)를 검증하는 verifier 에이전트를 만든다.

    통과 시 exit_loop()을 호출해 LoopAgent를 즉시 종료한다.
    불통과 시 retry_hint_key에 재검색 지시를 남기고 그냥 끝낸다
    (도구 호출을 안 하므로 LoopAgent가 다음 iteration으로 넘어간다).

    check_own_actions_only=True(context_verifier 전용): context_agent
    instruction에 "본인 발언/행동만 채택, 순수 평가·비판은 제외"를 넣었지만
    실제 재현에서 지켜지지 않는 케이스가 있어(여론조사·야당 비판이 여전히
    섞여 나옴) 여기서 이중으로 검사한다. speech/action은 애초에 "본인의
    발언/법안"만 다루는 게 목적이라 이 문제가 해당되지 않는다.
    """
    own_actions_check = (
        """
        4) 본인 발언/행동 검사(context_info 전용): 이 서비스는 대상 인물
        본인의 입장 변화를 보여주는 게 목적이다. [context_info]에 여론조사
        결과, 제3자(야당/시민단체/평론가 등)의 평가·비판만 있고 대상 인물
        본인의 직접 발언·행동 인용이 전혀 없는 섹션이 있다면, 그것도 근거
        불분명으로 판정한다. retry_hint에 "본인이 직접 한 말·행동이 인용된
        기사만 남기고, 순수 여론조사·제3자 평가는 제외하라"고 남겨라.

        4-1) 간접 재인용 주의: 대상 인물의 발언이 따옴표로 등장하더라도,
        그 문장 전체의 실제 화자·행위 주체가 제3자인 경우가 있다(예: "OOO
        평론가는 이 대통령의 '정부 이기는 시장이 없다'는 발언을 두고 '권력의
        오만'이라 비판했다" — 따옴표 속 문구는 대상 인물이 한 말이지만, 이
        문장 전체는 OOO 평론가가 그 발언을 인용하며 비판한 기사이지 대상
        인물이 그 시점에 직접 발언·행동한 기사가 아니다). [context_info]가
        이런 재인용을 마치 대상 인물이 그 시점에 직접 발언한 것처럼
        ("~라는 취지의 발언을 남겼다", "~라고 말했다") 다시 서술했다면, 이것도
        근거 불분명으로 판정한다. 핵심 동사(말했다/발언했다/주장했다 등)의
        주어가 대상 인물 자신인지, 그 발언을 인용하며 논평하는 제3자인지
        반드시 구분해서 판단하라. retry_hint에 "이 발언은 제3자가 인용·비판한
        기사에서 나온 것이지 대상 인물이 직접 한 발언이 아니다 — 대상 인물이
        스스로 발언·행동한 것으로 명시된 기사만 남기라"고 남겨라."""
        if check_own_actions_only
        else ""
    )
    return Agent(
        name=name,
        model=_model,
        tools=[exit_loop],
        instruction=f"""
        아래는 소스 에이전트가 만든 결과다.

        [{info_key}]
        {{{info_key}?}}

        이 내용이 실제 도구(국회 API/벡터DB/뉴스 검색) 호출 결과에 근거하는지
        판단하고, exit_loop 호출 여부를 결정하라.

        "근거 불분명(hallucination 의심)"으로 판정하는 경우:
        1) 예고만 하고 결과 없음 — 구체적 날짜·회의명·인용문 없이 "검색하겠습니다",
        "~를 진행합니다"처럼 앞으로 할 일만 서술하고 실제 산출물(기사 제목·발언
        인용·법안 번호 등)이 없는 경우. "~일 것으로 보입니다" 같은 추측성 표현으로
        사실을 서술하는 것도 포함된다.
        2) 관련성 부족 — 검색은 대상 이름이 "포함된" 결과를 줄 뿐 실제로 그
        인물/사안을 다룬다는 보장은 없다(예: "홍길동"이 선거법 판례의 가상
        사례나 전단지 시안 예시명으로만 등장). 이런 무관한 근거를 마치 대상
        인물의 실제 발언·행보인 것처럼 서술했다면 불분명으로 판정하고
        retry_hint에 "이름만 일치하는 무관한 결과는 제외하고 실제 검색 대상을
        다룬 근거만 남기라"고 남겨라.
        3) 사실성을 자의적으로 의심함 — [{info_key}]에 담긴 날짜·인물·직책이
        너(검증자) 자신의 학습 시점 지식과 달라 보이는 건 네가 모르는 최신
        사실일 뿐 hallucination이 아니다. "가상 시나리오로 보인다"류 코멘트가
        [{info_key}] 안에 있다면 그 코멘트 자체가 소스 에이전트의 잘못이니
        불분명으로 판정하고 retry_hint에 "검색 결과의 사실성을 판단하지 말고
        있는 그대로 전달하라"고 남겨라. 단, 사실 서술 자체(구체적 기사·발언·
        법안 인용)를 근거 없음으로 취급하지는 마라.{own_actions_check}

        "근거 있음"으로 판정하는 경우: 위 4가지 문제가 없는 경우. "정보가
        없습니다/조회 결과가 없습니다"처럼 실제로 조회했는데 결과가 없다고
        명시한 경우, 또는 [{info_key}]가 완전히 비어 있는 경우도 hallucination이
        아니라 정상 응답이므로 근거 있음으로 판단한다(1번의 "예고만 하고 끝남"과
        다르다 — 실제 조회 여부가 명시된 경우만 해당).

        - 근거가 있다고 판단되면: exit_loop 도구를 호출하라. 다른 텍스트는 출력하지 마라.
        - 근거가 불분명하면: exit_loop을 호출하지 말고, 어떤 부분이 불분명한지와
          다음 시도에서 어떤 키워드·조건으로 다시 검색해야 하는지를 한 문단으로
          {retry_hint_key} 값으로 출력하라.
        """,
        output_key=retry_hint_key,
    )


speech_verifier = _make_verifier("speech_verifier", "speech_info", "speech_retry_hint")
action_verifier = _make_verifier("action_verifier", "action_info", "action_retry_hint")
context_verifier = _make_verifier(
    "context_verifier", "context_info", "context_retry_hint", check_own_actions_only=True
)

# context_agent가 search_news를 호출할 때마다 실제 반환 URL을 session state에 기록.
context_agent.after_tool_callback = _record_search_news_urls

# context_verifier: LLM 판단 전에 URL 대조부터 수행 (가짜 URL이면 LLM 호출 없이 바로 불통과).
context_verifier.before_agent_callback = _make_url_check_callback(
    known_urls_key="context_search_urls",
    info_key="context_info",
    retry_hint_key="context_retry_hint",
)

# speech_verifier: LLM 판단 전에 실제 MCP 전체 발언과 결정적으로 대조한다.
speech_verifier.before_agent_callback = _check_speech_against_mcp

# action_verifier: LLM 판단 전에 실제 MCP 표결 필드와 결정적으로 대조한다.
action_verifier.before_agent_callback = _check_action_against_mcp

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
