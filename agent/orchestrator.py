"""ADK 에이전트 오케스트레이션 진입점.

플로우(스펙 7번): 질의 검증 -> 의도 분류/라우팅 -> 소스별 툴 호출
-> 결과 통합 -> 가드레일 검사 -> 출처 라벨링 -> 반환

TODO(C): 라우팅 로직, 툴 병렬 호출, 재시도/에러 처리, 가드레일 연결 구현.
참고: 복합 질의(국회API+RAG+웹검색 동시 호출)는 ADK ParallelAgent,
단일 경로 파이프라인은 SequentialAgent로 구성하는 걸 검토.
도구는 mcp_server/를 MCPToolset(StdioServerParameters)로 연결해서 씀.
"""


def run(question: str, member_name: str | None = None, keyword: str | None = None) -> dict:
    """반환 형식: {"answer": str, "sources": list[dict]}"""
    raise NotImplementedError
