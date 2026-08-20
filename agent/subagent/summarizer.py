from google.adk.agents import Agent
from agent.model import MODEL_NAME

summarizer = Agent(
    name='summarizer',
    model = MODEL_NAME,
    instruction = """
    수집된 정보를 종합하여 정리하세요.
    - api: {api_info}
    - rag: {rag_info}
    - news: {news_info}
    없는 내용을 만들지 말고 반드시 검증하여 정리하세요.
    """,
    output_key="summary",
)