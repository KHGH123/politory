"""벡터 검색. VertexAiSearchTool(raw 결과 모드)로 의원/키워드 필터링해서 조회.

가드레일 원칙상 LLM이 자동 요약한 답변이 아니라, 원문 스니펫+메타데이터를
그대로 받아서 백엔드가 시간순으로 조립해야 함 (agent/orchestrator.py 참고).
"""


def search(query: str, member_name: str | None = None, keyword: str | None = None, top_k: int = 5) -> list[dict]:
    raise NotImplementedError
