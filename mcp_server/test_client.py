import asyncio
import os

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


async def main():
    mcp_url = os.environ["MCP_URL"]
    token = os.getenv("ID_TOKEN")

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    transport = StreamableHttpTransport(
        url=mcp_url,
        headers=headers,
    )

    async with Client(transport) as client:
        tools = await client.list_tools()

        print("=== Tools ===")
        for tool in tools:
            print(tool.name)

        result = await client.call_tool(
            "search_speeches",
            {"query": os.getenv("TEST_QUERY", "국회 회의록"), "page_size": 3},
        )

        print("\n=== Result ===")
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
