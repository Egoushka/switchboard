"""Size of a route's tools/list per server. Usage: MCP_KEY=... uv run scripts/measure.py <url>"""

import collections
import json
import os
import sys

import anyio
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client


async def main(url: str) -> None:
    headers = {"Authorization": f"Bearer {os.environ['MCP_KEY']}"}
    async with (
        create_mcp_http_client(headers=headers) as http,
        Client(streamable_http_client(url, http_client=http)) as client,
    ):
        tools = (await client.list_tools()).tools
    per: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for t in tools:
        size = len(json.dumps({"name": t.name, "description": t.description, "inputSchema": t.input_schema}))
        per[t.name.split("_", 1)[0]][0] += 1
        per[t.name.split("_", 1)[0]][1] += size
    for server, (n, size) in sorted(per.items(), key=lambda kv: -kv[1][1]):
        print(f"{server:15} {n:4} tools {size:8} chars")
    total = sum(size for _, size in per.values())
    print(f"{'TOTAL':15} {len(tools):4} tools {total:8} chars (~{total // 4} tokens)")


anyio.run(main, sys.argv[1])
