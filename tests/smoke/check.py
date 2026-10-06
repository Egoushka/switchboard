"""Smoke check through the real agentgateway: uv run python tests/smoke/check.py"""

import json

import anyio
import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

GATEWAY = "http://127.0.0.1:13000"
TELEGRAM_LOG = "http://127.0.0.1:18081/log"


def connect(route: str, key: str):
    headers = {"Authorization": f"Bearer {key}", "x-switchboard-client": "spoofed"}
    return create_mcp_http_client(headers=headers), lambda http: Client(
        streamable_http_client(f"{GATEWAY}/mcp/{route}", http_client=http)
    )


async def read_only() -> None:
    http, client = connect("lab-ro", "smoke-client-key")
    async with http as h, client(h) as c:
        names = [t.name for t in (await c.list_tools()).tools]
        assert names == ["lab_search", "lab_describe", "lab_read", "lab_more"], names
        hits = (await c.call_tool("lab_search", {"query": "sleep"})).content[0].text
        assert hits.startswith("oura_sleep [read]"), hits
        got = (await c.call_tool("lab_read", {"tool": "oura_sleep"})).content[0].text
        assert json.loads(got) == {"day": "2026-09-27", "score": 81}, got
        refused = await c.call_tool("lab_write", {"tool": "oura_add", "args": {"text": "x"}, "reason": "r"})
        assert refused.is_error, refused


async def writes() -> None:
    http, client = connect("lab-rw", "smoke-writer-key")
    async with http as h, client(h) as c:
        names = [t.name for t in (await c.list_tools()).tools]
        assert names == ["lab_search", "lab_describe", "lab_read", "lab_write", "lab_more"], names
        hits = (await c.call_tool("lab_search", {"query": "add store text"})).content[0].text
        assert "oura_add [write]" in hits, hits

        async def added() -> list[str]:
            return json.loads((await c.call_tool("lab_read", {"tool": "oura_added"})).content[0].text)

        approved = await c.call_tool("lab_write", {"tool": "oura_add", "args": {"text": "approve-me"}, "reason": "smoke"})
        assert not approved.is_error and json.loads(approved.content[0].text) == {"added": "approve-me"}, approved
        assert await added() == ["approve-me"]

        denied = await c.call_tool("lab_write", {"tool": "oura_add", "args": {"text": "deny-me"}, "reason": "smoke"})
        assert denied.is_error and denied.content[0].text.startswith("denied; nothing was executed"), denied
        assert await added() == ["approve-me"]  # the denied write never reached the upstream

    async with httpx2.AsyncClient() as plain:
        phone = (await plain.get(TELEGRAM_LOG)).json()
    assert len(phone["messages"]) == 2, phone
    assert all("smoke-writer" in m and "spoofed" not in m for m in phone["messages"]), phone  # the gateway names the client
    assert any("approved — ok" in e for e in phone["edits"]) and any("denied" in e for e in phone["edits"]), phone


async def until_up() -> None:
    """The gateway and switchboard start after check.py is launched: wait for the first answer."""
    http, client = connect("lab-ro", "smoke-client-key")
    with anyio.fail_after(90):
        while True:
            try:
                async with http as h, client(h) as c:
                    await c.list_tools()
                return
            except Exception:  # noqa: BLE001 - anything but an answer means not up yet
                await anyio.sleep(1)
                http, client = connect("lab-ro", "smoke-client-key")


async def main() -> None:
    await until_up()
    await read_only()
    await writes()
    print("smoke OK")


anyio.run(main)
