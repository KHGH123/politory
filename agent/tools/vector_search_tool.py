"""RAG 검색 툴 (Vertex AI Search). rag/retriever.py를 ADK 툴 형태로 감싼다. mcp_server/로 노출 예정."""


def search_speeches(query: str, member_name: str | None = None, keyword: str | None = None) -> list[dict]:
    raise NotImplementedError
