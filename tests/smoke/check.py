"""Smoke check through the real agentgateway: uv run python tests/smoke/check.py"""

import json

import anyio
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client


async def main() -> None:
    headers = {"Authorization": "Bearer smoke-client-key", "x-switchboard-client": "spoofed"}
    async with (
        create_mcp_http_client(headers=headers) as http,
        Client(streamable_http_client("http://127.0.0.1:13000/mcp/lab-ro", http_client=http)) as client,
    ):
        names = [t.name for t in (await client.list_tools()).tools]
        assert names == ["lab_search", "lab_describe", "lab_read", "lab_more"], names
        hits = (await client.call_tool("lab_search", {"query": "sleep"})).content[0].text
        assert hits.startswith("oura_sleep [read]"), hits
        got = (await client.call_tool("lab_read", {"tool": "oura_sleep"})).content[0].text
        assert json.loads(got) == {"day": "2026-09-27", "score": 81}, got
    print("smoke OK")


anyio.run(main)
