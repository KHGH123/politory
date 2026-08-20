# mcp_server

agent/tools/의 국회 API·RAG·웹검색 도구를 MCP 서버로 노출하는 자리.
(C가 직접 구현 — 구조만 잡아둠)

- `agent/agent.py`에서는 `MCPToolset` + `StdioServerParameters`로 이 서버에 연결.
- 서버 쪽은 `FunctionTool`로 기존 함수를 래핑해 stdio로 제공하는 패턴.
