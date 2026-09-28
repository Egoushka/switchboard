import json
import re
import socket
from contextlib import asynccontextmanager

import anyio
import httpx2
import mcp_types as types
import pytest
import uvicorn
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer
from mcp.shared._httpx_utils import create_mcp_http_client

from switchboard.approval import Approver
from switchboard.config import Approval, Config, Results, Scope, Server
from switchboard.server import build_app
from switchboard.upstream import Upstream

TOKEN = "sb-token"


def fake_upstream():
    server = MCPServer("fake")
    writes = []

    async def oura_sleep(day: str = "") -> types.CallToolResult:
        data = {"day": day or "2026-09-27", "score": 81, "contributors": None, "tags": []}
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(data))], structured_content=data)

    async def oura_big() -> types.CallToolResult:
        return types.CallToolResult(content=[types.TextContent(type="text", text="x" * 20_000)])

    async def oura_fail() -> types.CallToolResult:
        return types.CallToolResult(content=[types.TextContent(type="text", text="rate limited")], is_error=True)

    async def notes_add(text: str) -> types.CallToolResult:
        writes.append(text)
        return types.CallToolResult(content=[types.TextContent(type="text", text="added")])

    server.add_tool(oura_sleep, name="oura_sleep", description="One night's sleep score and contributors.")
    server.add_tool(oura_big, name="oura_big", description="A very large sleep export.")
    server.add_tool(oura_fail, name="oura_fail", description="Always fails.")
    server.add_tool(notes_add, name="notes_add", description="Add a note.")
    return server, writes


def config():
    return Config(
        upstream="http://unused",
        ingress_token=TOKEN,
        approval=Approval(telegram_token="tg", approver_id=42, timeout_s=1.0),
        results=Results(),
        scopes={
            "homelab": Scope("homelab", "/mcp/homelab", "k", True, 1.0),
            "homelab-ro": Scope("homelab-ro", "/mcp/homelab", "k", False, 1.0),
        },
        servers={"oura": Server(about="Oura ring — sleep", read=(re.compile("."),)), "notes": Server(about="Notes")},
    )


class FakeTelegram:
    """Answers every approval request with `policy` through the approver's own poll loop."""

    def __init__(self):
        self.policy, self.sent, self.edits, self._n = "approve", [], [], 0
        self._tx, self._rx = anyio.create_memory_object_stream(100)

    async def send(self, chat_id, text, buttons):
        self.sent.append(text)
        rid = buttons[0][1].partition(":")[2]
        if self.policy != "ignore":
            self._n += 1
            data = ("a:" if self.policy == "approve" else "d:") + rid
            await self._tx.send({"update_id": self._n, "callback_query": {"id": f"cb{self._n}", "from": {"id": 42}, "data": data}})
        return len(self.sent)

    async def edit(self, chat_id, message_id, text):
        self.edits.append(text)

    async def answer(self, callback_id, text):
        pass

    async def updates(self, offset, timeout):
        return [await self._rx.receive()]


def inprocess(server):
    @asynccontextmanager
    async def connect():
        async with Client(server) as client:
            yield client

    return connect


@asynccontextmanager
async def switchboard():
    upstream_server, writes = fake_upstream()
    telegram = FakeTelegram()
    cfg = config()
    upstreams = {name: Upstream(name, inprocess(upstream_server)) for name in cfg.scopes}
    app = build_app(cfg, upstreams, Approver(telegram, 42))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    async with anyio.create_task_group() as tg:
        tg.start_soon(server.serve, [sock])
        with anyio.fail_after(10):
            while not server.started:
                await anyio.sleep(0.02)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}", telegram, writes
        server.should_exit = True


@asynccontextmanager
async def mcp(base, scope, client_name="laptop"):
    headers = {"Authorization": f"Bearer {TOKEN}", "x-switchboard-client": client_name}
    async with (
        create_mcp_http_client(headers=headers) as http,
        Client(streamable_http_client(f"{base}/mcp/{scope}", http_client=http)) as client,
    ):
        yield client


def text(result):
    return result.content[0].text


@pytest.mark.anyio
async def test_needs_the_bearer_but_healthz_does_not():
    async with switchboard() as (base, _, _), httpx2.AsyncClient() as http:
        assert (await http.get(f"{base}/healthz")).status_code == 200
        assert (await http.post(f"{base}/mcp/homelab", json={})).status_code == 401
        wrong = {"Authorization": "Bearer nope"}
        assert (await http.post(f"{base}/mcp/homelab", json={}, headers=wrong)).status_code == 401


@pytest.mark.anyio
async def test_read_only_scope_lists_four_small_tools_with_the_catalog():
    async with switchboard() as (base, _, _), mcp(base, "homelab-ro") as client:
        tools = (await client.list_tools()).tools
    assert [t.name for t in tools] == ["search", "describe", "read", "more"]
    search = next(t for t in tools if t.name == "search")
    assert "- notes (1 tools): Notes" in search.description and "- oura (3 tools): Oura ring — sleep" in search.description
    assert len(json.dumps([t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools])) <= 8000  # S1


@pytest.mark.anyio
async def test_search_describe_and_read_with_a_query():
    async with switchboard() as (base, _, _), mcp(base, "homelab-ro") as client:
        hits = text(await client.call_tool("search", {"query": "sleep score"}))
        described = json.loads(text(await client.call_tool("describe", {"tools": ["oura_sleep", "nope_x"]})))
        full = text(await client.call_tool("read", {"tool": "oura_sleep", "args": {"day": "2026-09-26"}}))
        score = text(await client.call_tool("read", {"tool": "oura_sleep", "query": "score"}))
    assert hits.splitlines()[0].startswith("oura_sleep [read] — One night's sleep score")
    assert described[0]["tool"] == "oura_sleep" and described[0]["verb"] == "read" and "input_schema" in described[0]
    assert "unknown tool" in described[1]["error"]
    assert json.loads(full) == {"day": "2026-09-26", "score": 81}  # nulls and empties dropped
    assert score == "81"


@pytest.mark.anyio
async def test_read_refuses_a_write_tool_and_suggests_for_an_unknown_one():
    async with switchboard() as (base, _, _), mcp(base, "homelab-ro") as client:
        write = await client.call_tool("read", {"tool": "notes_add", "args": {"text": "x"}})
        unknown = await client.call_tool("read", {"tool": "oura_slep"})
    assert write.is_error and "is a write tool" in text(write)
    assert unknown.is_error and "did you mean: oura_" in text(unknown)


@pytest.mark.anyio
async def test_a_big_result_pages_through_more():
    async with switchboard() as (base, _, _), mcp(base, "homelab-ro") as client:
        first = text(await client.call_tool("read", {"tool": "oura_big", "max_chars": 1000}))
        rid = re.search(r'result_id="(r_[^"]+)"', first).group(1)
        second = text(await client.call_tool("more", {"result_id": rid, "offset": 1000}))
        end = text(await client.call_tool("more", {"result_id": rid, "offset": 99_999}))
        gone = await client.call_tool("more", {"result_id": "r_missing", "offset": 0})
    assert first.startswith("x" * 1000) and "truncated: 1000 of 20000" in first
    assert second.startswith("x" * 1000) and "offset=2000" in second
    assert end == "[end of result: 20000 chars]"
    assert gone.is_error and "expired" in text(gone)


@pytest.mark.anyio
async def test_an_upstream_tool_error_stays_an_error():
    async with switchboard() as (base, _, _), mcp(base, "homelab-ro") as client:
        result = await client.call_tool("read", {"tool": "oura_fail"})
    assert result.is_error and text(result) == "rate limited"


@pytest.mark.anyio
async def test_out_of_range_numbers_are_clamped():
    async with switchboard() as (base, _, _), mcp(base, "homelab-ro") as client:
        tiny = text(await client.call_tool("read", {"tool": "oura_big", "max_chars": 0}))
        rid = re.search(r'result_id="(r_[^"]+)"', tiny).group(1)
        again = text(await client.call_tool("more", {"result_id": rid, "offset": -5}))
        many = text(await client.call_tool("search", {"query": "sleep", "limit": 1000}))
    assert tiny.startswith("x\n…[truncated: 1 of 20000")
    assert again.startswith("x\n…[truncated: 1 of 20000")
    assert len(many.splitlines()) <= 20


@pytest.mark.anyio
async def test_writable_scope_adds_write():
    async with switchboard() as (base, _, _), mcp(base, "homelab") as client:
        tools = (await client.list_tools()).tools
    assert [t.name for t in tools] == ["search", "describe", "read", "write", "more"]
    write = next(t for t in tools if t.name == "write")
    assert write.annotations.read_only_hint is False and write.annotations.destructive_hint is True


@pytest.mark.anyio
async def test_write_runs_once_after_approval_and_is_audited(caplog):
    caplog.set_level("INFO", logger="switchboard.audit")
    async with switchboard() as (base, telegram, writes), mcp(base, "homelab", client_name="claude-desktop") as client:
        result = await client.call_tool("write", {"tool": "notes_add", "args": {"text": "buy milk"}, "reason": "Yehor asked"})
    assert not result.is_error and text(result) == "added"
    assert writes == ["buy milk"]
    assert "notes_add" in telegram.sent[0] and "claude-desktop" in telegram.sent[0] and "Yehor asked" in telegram.sent[0]
    assert "approved — ok" in telegram.edits[-1]
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "switchboard.audit"]
    assert lines[-1]["decision"] == "approved" and lines[-1]["outcome"] == "ok" and lines[-1]["client"] == "claude-desktop"
    assert "buy milk" not in json.dumps(lines) and len(lines[-1]["args_sha256"]) == 64


@pytest.mark.anyio
@pytest.mark.parametrize(("policy", "decision"), [("deny", "denied"), ("ignore", "expired")])
async def test_write_denied_or_expired_runs_nothing(policy, decision):
    async with switchboard() as (base, telegram, writes):
        telegram.policy = policy
        async with mcp(base, "homelab") as client:
            result = await client.call_tool("write", {"tool": "notes_add", "args": {"text": "x"}, "reason": "r"})
    assert result.is_error and text(result).startswith(f"{decision}; nothing was executed")
    assert writes == []


@pytest.mark.anyio
async def test_write_is_not_offered_on_a_read_only_scope():
    async with switchboard() as (base, _, writes), mcp(base, "homelab-ro") as client:
        result = await client.call_tool("write", {"tool": "notes_add", "args": {"text": "x"}, "reason": "r"})
    assert result.is_error and writes == []


@pytest.mark.anyio
async def test_a_result_id_does_not_work_in_another_scope():
    async with switchboard() as (base, _, _):
        async with mcp(base, "homelab-ro") as client:
            first = text(await client.call_tool("read", {"tool": "oura_big", "max_chars": 1000}))
        rid = re.search(r'result_id="(r_[^"]+)"', first).group(1)
        async with mcp(base, "homelab") as client:
            other = await client.call_tool("more", {"result_id": rid, "offset": 1000})
    assert other.is_error and "expired" in text(other)
