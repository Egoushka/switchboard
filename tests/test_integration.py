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

from switchboard import server
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

    async def notes_slow_add(text: str) -> types.CallToolResult:
        await anyio.sleep(0.5)
        writes.append(text)
        return types.CallToolResult(content=[types.TextContent(type="text", text="added slowly")])

    server.add_tool(oura_sleep, name="oura_sleep", description="One night's sleep score and contributors.")
    server.add_tool(oura_big, name="oura_big", description="A very large sleep export.")
    server.add_tool(oura_fail, name="oura_fail", description="Always fails.")
    server.add_tool(notes_add, name="notes_add", description="Add a note.")
    server.add_tool(notes_slow_add, name="notes_slow_add", description="Add a note, slowly.")
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
async def switchboard(connect=None, progress_every_s=15):
    upstream_server, writes = fake_upstream()
    telegram = FakeTelegram()
    cfg = config()
    upstreams = {name: Upstream(name, connect or inprocess(upstream_server)) for name in cfg.scopes}
    app = build_app(cfg, upstreams, Approver(telegram, 42, progress_every_s=progress_every_s))
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
    assert "- notes (2 tools): Notes" in search.description and "- oura (3 tools): Oura ring — sleep" in search.description
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


@pytest.mark.anyio
async def test_an_approved_write_finishes_and_is_audited_even_if_the_client_gives_up(caplog):
    caplog.set_level("INFO", logger="switchboard.audit")
    async with switchboard() as (base, telegram, writes):
        async with mcp(base, "homelab") as client:
            with anyio.move_on_after(0.2):  # the client gives up while the approved write runs
                await client.call_tool("write", {"tool": "notes_slow_add", "args": {"text": "x"}, "reason": "r"})
        with anyio.fail_after(5):
            while not writes or not any("approved — ok" in e for e in telegram.edits):
                await anyio.sleep(0.05)
    assert writes == ["x"]
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "switchboard.audit"]
    assert [line["outcome"] for line in lines] == ["running", "ok"]
    assert lines[0]["approval_id"] == lines[1]["approval_id"]


@pytest.mark.anyio
async def test_a_bad_query_is_refused_before_asking():
    async with switchboard() as (base, telegram, writes), mcp(base, "homelab") as client:
        args = {"tool": "notes_add", "args": {"text": "x"}, "reason": "r", "query": "[[["}
        result = await client.call_tool("write", args)
    assert result.is_error and "bad query" in text(result) and "nothing was executed" in text(result)
    assert telegram.sent == [] and writes == []


@pytest.mark.anyio
async def test_a_query_that_cannot_apply_after_the_write_still_reports_the_write():
    async with switchboard() as (base, _, writes), mcp(base, "homelab") as client:
        args = {"tool": "notes_add", "args": {"text": "x"}, "reason": "r", "query": "id"}
        result = await client.call_tool("write", args)
    assert not result.is_error and writes == ["x"]
    assert "the write ran" in text(result) and "added" in " ".join(c.text for c in result.content)


@pytest.mark.anyio
async def test_a_write_too_large_to_show_is_refused_before_anything_runs():
    big = {f"k{i}": "v" * 150 for i in range(40)}
    async with switchboard() as (base, telegram, writes), mcp(base, "homelab") as client:
        result = await client.call_tool("write", {"tool": "notes_add", "args": big, "reason": "r"})
    assert result.is_error and "too large to show" in text(result)
    assert telegram.sent == [] and writes == []


class Flaky:
    """A session that lists tools but fails every call: it raises, or it never answers."""

    def __init__(self, client, mode):
        self._client, self._mode = client, mode

    async def list_tools(self, **kwargs):
        return await self._client.list_tools(**kwargs)

    async def call_tool(self, *args, **kwargs):
        if self._mode == "raise":
            raise OSError("connection reset")
        await anyio.sleep_forever()


def flaky(mode):
    upstream_server, _ = fake_upstream()

    @asynccontextmanager
    async def connect():
        async with Client(upstream_server) as client:
            yield Flaky(client, mode)

    return connect


def audit_lines(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "switchboard.audit"]


@pytest.mark.anyio
async def test_a_write_whose_upstream_call_raises_after_approval_reports_the_result_as_unknown(caplog):
    caplog.set_level("INFO", logger="switchboard.audit")
    async with switchboard(connect=flaky("raise")) as (base, telegram, writes), mcp(base, "homelab") as client:
        result = await client.call_tool("write", {"tool": "notes_add", "args": {"text": "x"}, "reason": "r"})
    assert result.is_error and writes == []
    assert "result is unknown" in text(result) and "may or may not have happened" in text(result)
    assert "result unknown: upstream homelab unavailable" in telegram.edits[-1]
    lines = audit_lines(caplog)
    assert [line["outcome"] for line in lines] == ["running", "unknown"]
    assert {line["decision"] for line in lines} == {"approved"}


@pytest.mark.anyio
async def test_a_write_that_outlasts_the_timeout_after_approval_reports_the_result_as_unknown(caplog, monkeypatch):
    caplog.set_level("INFO", logger="switchboard.audit")
    monkeypatch.setattr(server, "WRITE_TIMEOUT_S", 0.2)
    async with switchboard(connect=flaky("hang")) as (base, telegram, _), mcp(base, "homelab") as client:
        result = await client.call_tool("write", {"tool": "notes_add", "args": {"text": "x"}, "reason": "r"})
    assert result.is_error and "result is unknown" in text(result) and "did not answer in 0.2 s" in text(result)
    assert "result unknown: the upstream did not answer in 0.2 s" in telegram.edits[-1]
    assert [line["outcome"] for line in audit_lines(caplog)] == ["running", "unknown"]


@pytest.mark.anyio
async def test_the_client_gets_progress_notifications_while_waiting_for_a_tap():
    seen = []

    async def on_progress(progress, total, message):
        seen.append((progress, total, message))

    async with switchboard(progress_every_s=0.1) as (base, telegram, writes), mcp(base, "homelab") as client:
        telegram.policy = "ignore"
        result = await client.call_tool(
            "write", {"tool": "notes_add", "args": {"text": "x"}, "reason": "r"}, progress_callback=on_progress
        )
    assert result.is_error and text(result).startswith("expired") and writes == []
    assert len(seen) >= 3
    assert {total for _, total, _ in seen} == {1.0} and {m for _, _, m in seen} == {"waiting for Yehor's approval"}
    assert [p for p, _, _ in seen] == sorted(p for p, _, _ in seen) and seen[-1][0] > 0.1


def refusing(fail_first):  # start-up makes one connect per scope
    upstream_server, _ = fake_upstream()
    state = {"connects": 0}

    @asynccontextmanager
    async def connect():
        state["connects"] += 1
        if state["connects"] <= fail_first:
            raise OSError("connection refused")
        async with Client(upstream_server) as client:
            yield client

    return connect


@pytest.mark.anyio
async def test_the_catalog_falls_back_to_config_lines_when_the_gateway_is_down_at_start():
    async with switchboard(connect=refusing(fail_first=2)) as (base, _, _), mcp(base, "homelab-ro") as client:
        tools = (await client.list_tools()).tools
        search = next(t for t in tools if t.name == "search")
        recovered = text(await client.call_tool("search", {"query": "sleep"}))
    assert "- notes: Notes" in search.description and "- oura: Oura ring — sleep" in search.description
    assert "tools)" not in search.description
    assert recovered.startswith("oura_sleep [read]")  # the gateway came up: calls work, only the lines stay as they were


@pytest.mark.anyio
async def test_the_gateway_stays_down_without_taking_switchboard_down():
    async with switchboard(connect=refusing(fail_first=10**6)) as (base, _, _), mcp(base, "homelab-ro") as client:
        result = await client.call_tool("search", {"query": "sleep"})
        listed = await client.list_tools()
    assert [t.name for t in listed.tools] == ["search", "describe", "read", "more"]
    assert result.is_error and "upstream homelab-ro unavailable" in text(result)


@pytest.mark.anyio
async def test_a_gateway_that_never_answers_is_given_up_on_after_the_startup_wait(monkeypatch):
    @asynccontextmanager
    async def hangs():
        await anyio.sleep_forever()
        yield

    monkeypatch.setattr(server, "STARTUP_WAIT_S", 0.3)
    started = anyio.current_time()
    async with switchboard(connect=hangs) as (base, _, _), mcp(base, "homelab-ro") as client:
        search = next(t for t in (await client.list_tools()).tools if t.name == "search")
    assert 0.3 <= anyio.current_time() - started < 8
    assert "- oura: Oura ring — sleep" in search.description and "tools)" not in search.description


def sample(name, **labels):
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.mark.anyio
async def test_metrics_count_calls_chars_approvals_and_the_catalog():
    def now():
        return {
            "read_ok": sample("switchboard_calls_total", scope="homelab", server="oura", verb="read", outcome="ok"),
            "read_error": sample("switchboard_calls_total", scope="homelab", server="oura", verb="read", outcome="error"),
            "write_ok": sample("switchboard_calls_total", scope="homelab", server="notes", verb="write", outcome="ok"),
            "upstream": sample("switchboard_result_chars_total", scope="homelab", server="oura", stage="upstream"),
            "returned": sample("switchboard_result_chars_total", scope="homelab", server="oura", stage="returned"),
            "approved": sample("switchboard_approvals_total", scope="homelab", decision="approved"),
            "denied": sample("switchboard_approvals_total", scope="homelab", decision="denied"),
            "waits": sample("switchboard_approval_wait_seconds_count"),
        }

    before = now()
    async with switchboard() as (base, telegram, _), mcp(base, "homelab") as client:
        await client.call_tool("read", {"tool": "oura_sleep"})
        await client.call_tool("read", {"tool": "oura_fail"})
        await client.call_tool("read", {"tool": "oura_big", "max_chars": 1000})
        await client.call_tool("write", {"tool": "notes_add", "args": {"text": "x"}, "reason": "r"})
        telegram.policy = "deny"
        await client.call_tool("write", {"tool": "notes_add", "args": {"text": "y"}, "reason": "r"})
    after = now()
    delta = {k: after[k] - before[k] for k in before}
    assert delta["read_ok"] == 2 and delta["read_error"] == 1  # oura_big is a successful read
    assert delta["write_ok"] == 1 and delta["approved"] == 1 and delta["denied"] == 1 and delta["waits"] == 2
    assert delta["upstream"] > 20_000 and 0 < delta["returned"] < delta["upstream"]
    assert sample("switchboard_catalog_tools", scope="homelab") == 5


@pytest.mark.anyio
async def test_metrics_count_upstream_failures():
    labels = {"scope": "homelab-ro"}
    before = sample("switchboard_upstream_errors_total", **labels)
    async with switchboard(connect=refusing(fail_first=10**6)) as (base, _, _), mcp(base, "homelab-ro") as client:
        await client.call_tool("search", {"query": "sleep"})
    assert sample("switchboard_upstream_errors_total", **labels) > before
