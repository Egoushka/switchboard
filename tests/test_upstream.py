from contextlib import asynccontextmanager

import anyio
import pytest
from mcp.client import Client
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError

from switchboard.config import Server
from switchboard.upstream import ToolCache, Upstream, UpstreamError


def fake_upstream():
    server = MCPServer("fake")
    calls = []

    async def oura_sleep(day: str = "") -> str:
        calls.append(day)
        return '{"score": 80}'

    server.add_tool(oura_sleep, name="oura_sleep", description="Sleep score.")
    return server, calls


def connector(server, fail_first=0):
    state = {"connects": 0}

    @asynccontextmanager
    async def connect():
        state["connects"] += 1
        if state["connects"] <= fail_first:
            raise OSError("connection refused")
        async with Client(server) as client:
            yield client

    return connect, state


@pytest.mark.anyio
async def test_lists_and_calls_over_one_session():
    server, calls = fake_upstream()
    connect, state = connector(server)
    async with anyio.create_task_group() as tg:
        upstream = Upstream("homelab", connect)
        upstream.bind(tg)
        assert [t.name for t in await upstream.list_tools()] == ["oura_sleep"]
        result = await upstream.call_tool("oura_sleep", {"day": "2026-09-27"}, retry=True)
        assert result.content[0].text == '{"score": 80}' and calls == ["2026-09-27"]
        assert state["connects"] == 1
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_failed_connect_is_an_upstream_error_and_the_next_call_reconnects():
    server, _ = fake_upstream()
    connect, state = connector(server, fail_first=1)
    async with anyio.create_task_group() as tg:
        upstream = Upstream("homelab", connect)
        upstream.bind(tg)
        with pytest.raises(UpstreamError, match="homelab unavailable"):
            await upstream.list_tools()
        assert len(await upstream.list_tools()) == 1
        assert state["connects"] == 2
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_dead_session_is_replaced_for_reads_but_writes_are_not_retried():
    server, _ = fake_upstream()
    connect, state = connector(server)
    async with anyio.create_task_group() as tg:
        upstream = Upstream("homelab", connect)
        upstream.bind(tg)
        seen = []

        async def flaky(client):
            seen.append(client)
            if len(seen) == 1:
                raise RuntimeError("stream closed")
            return "ok"

        assert await upstream._request(flaky, retry=True) == "ok"
        assert state["connects"] == 2 and seen[0] is not seen[1]

        async def dead(client):
            raise RuntimeError("stream closed")

        with pytest.raises(UpstreamError):
            await upstream._request(dead, retry=False)
        assert state["connects"] == 2  # a write is never retried
        await upstream.list_tools()
        assert state["connects"] == 3  # the dropped session is replaced on the next call
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_json_rpc_error_is_reported_and_keeps_the_session():
    server, _ = fake_upstream()
    connect, state = connector(server)
    async with anyio.create_task_group() as tg:
        upstream = Upstream("homelab", connect)
        upstream.bind(tg)
        await upstream.list_tools()

        async def refused(client):
            raise MCPError(-32602, "invalid params: day")

        with pytest.raises(UpstreamError, match="invalid params: day"):
            await upstream._request(refused, retry=True)
        await upstream.list_tools()
        assert state["connects"] == 1
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_tool_cache_refreshes_after_ttl_and_keeps_the_last_good_catalog():
    server, _ = fake_upstream()
    connect, _ = connector(server)
    now = [0.0]
    async with anyio.create_task_group() as tg:
        upstream = Upstream("homelab", connect)
        upstream.bind(tg)
        cache = ToolCache("homelab", upstream, {"oura": Server(about="Oura")}, ttl_s=300, clock=lambda: now[0])
        tools, _ = await cache.get()
        assert tools["oura_sleep"].verb == "write"  # no read regex, annotations untrusted
        assert await cache.counts() == {"oura": 1}

        async def down():
            raise UpstreamError("upstream homelab unavailable")

        upstream.list_tools = down
        now[0] = 301
        stale, _ = await cache.get()
        assert stale is tools
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_counts_is_none_when_upstream_never_answered():
    async def never():
        raise UpstreamError("upstream homelab unavailable")

    upstream = Upstream("homelab", connector(fake_upstream()[0])[0])
    upstream.list_tools = never
    assert await ToolCache("homelab", upstream, {}).counts() is None

