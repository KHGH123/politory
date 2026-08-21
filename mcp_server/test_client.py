import asyncio
import os

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


async def main():
    mcp_url = os.environ["MCP_URL"]
    token = os.environ["ID_TOKEN"]

    transport = StreamableHttpTransport(
        url=mcp_url,
        headers={
            "Authorization": f"Bearer {token}"
        }
    )

    async with Client(transport) as client:
        tools = await client.list_tools()

        print("=== Tools ===")
        for tool in tools:
            print(tool.name)

        result = await client.call_tool(
            "search_actions",
            {"query": "테스트 정치인"}
        )

        print("\n=== Result ===")
        print(result)


if __name__ == "__main__":
    asyncio.run(main())