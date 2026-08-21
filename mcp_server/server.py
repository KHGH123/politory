from fastmcp import FastMCP

# MCP 서버 생성
mcp = FastMCP("mcp")


# 테스트용 Tool
@mcp.tool()
def search_actions(query: str) -> dict:
    """정치인의 행동 정보를 검색하는 테스트 Tool입니다."""
    return {
        "query": query,
        "results": [
            {
                "politician": "테스트 정치인",
                "action": "테스트 행동",
                "date": "2026-01-01"
            }
        ]
    }


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8080
    )