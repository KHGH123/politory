from google.adk.agents import Agent, ParallelAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types

def skip_if_not_routed(source_key: str):
    """route[source_key]가 False면 에이전트 실행 자체를 건너뛰는 콜백 팩토리"""
    def _callback(callback_context: CallbackContext):
        # session.state["route"] = {"api": false, "rag": true, "web": false}
        route = callback_context.state.get("route", {})
        if not route.get(source_key, False):
            return types.Content(
                parts=[types.Part(text="")],
            )
        return None # 정상 실행
    return _callback

api_search_agent = Agent(
    name="api_search_agent",
    tools=[],#get_bills, get_votes, get_member_info],
    instruction="국회 API로 법안/표결/의원정보를 조회한다.",
    before_agent_callback=skip_if_not_routed("api"),
    output_key='api_info',
)

rag_search_agent = Agent(
    name="rag_search_agent",
    tools=[],#search_speeches],
    instruction="벡터DB에서 관련 발언을 검색한다.",
    before_agent_callback=skip_if_not_routed("rag"),
    output_key='rag_info',
)

news_search_agent = Agent(
    name="news_search_agent",
    tools=[],#search_news],
    instruction="관련 뉴스 기사를 검색한다.",
    before_agent_callback=skip_if_not_routed("web"),
    output_key='news_info',
)

fetch = ParallelAgent(
    name='multi_info_fetcher',
    sub_agents=[api_search_agent, rag_search_agent, news_search_agent],
    description="""
    여러 정보를 동시에 수집
    """
)