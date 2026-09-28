import json
import socket
from contextlib import asynccontextmanager

import anyio
import mcp_types as types
import pytest
import uvicorn
from mcp.client import Client
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError

from switchboard.config import Server
from switchboard.upstream import ToolCache, Upstream, UpstreamError, http_session


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



@pytest.mark.anyio
@pytest.mark.parametrize(
    ("code", "message"),
    [(types.CONNECTION_CLOSED, "Connection closed"), (types.INVALID_REQUEST, "Session not found")],
)
async def test_a_lost_session_error_reconnects_for_reads(code, message):
    server, _ = fake_upstream()
    connect, state = connector(server)
    async with anyio.create_task_group() as tg:
        upstream = Upstream("homelab", connect)
        upstream.bind(tg)
        seen = []

        async def op(client):
            seen.append(client)
            if len(seen) == 1:
                raise MCPError(code, message)
            return "ok"

        assert await upstream._request(op, retry=True) == "ok"
        assert state["connects"] == 2 and seen[0] is not seen[1]
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_lost_session_on_a_write_is_dropped_but_not_retried():
    server, _ = fake_upstream()
    connect, state = connector(server)
    async with anyio.create_task_group() as tg:
        upstream = Upstream("homelab", connect)
        upstream.bind(tg)

        async def lost(client):
            raise MCPError(types.CONNECTION_CLOSED, "Connection closed")

        with pytest.raises(UpstreamError, match="homelab unavailable"):
            await upstream._request(lost, retry=False)
        assert state["connects"] == 1
        await upstream.list_tools()
        assert state["connects"] == 2
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_late_failure_on_an_old_session_does_not_drop_the_new_one():
    server, _ = fake_upstream()
    connect, state = connector(server)
    async with anyio.create_task_group() as tg:
        upstream = Upstream("homelab", connect)
        upstream.bind(tg)
        await upstream.list_tools()
        old = upstream._client
        late = anyio.Event()

        async def slow_failure(client):
            await late.wait()
            raise RuntimeError("stream closed")

        async def request_b():
            with pytest.raises(UpstreamError):
                await upstream._request(slow_failure, retry=False)

        async def fails_on_old(client):
            if client is old:
                raise RuntimeError("stream closed")
            return "ok"

        async with anyio.create_task_group() as inner:
            inner.start_soon(request_b)
            await anyio.sleep(0.05)  # B now waits on the old session
            assert await upstream._request(fails_on_old, retry=True) == "ok"  # A replaced it
            fresh = upstream._client
            late.set()  # B fails late, on the old session
        assert upstream._client is fresh and fresh is not None
        assert state["connects"] == 2
        tg.cancel_scope.cancel()


def _session_server():
    server = MCPServer("gateway")

    async def oura_sleep() -> str:
        return "ok"

    server.add_tool(oura_sleep, name="oura_sleep", description="x")
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return server.streamable_http_app(streamable_http_path="/mcp", transport_security=security)


def _legacy_only(holder):
    """Refuses server/discover, so the client falls back to initialize and holds an Mcp-Session-Id."""

    async def app(scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await holder["app"](scope, receive, send)
        body, more = b"", True
        while more:
            message = await receive()
            body += message.get("body", b"")
            more = message.get("more_body", False)
        parsed = json.loads(body) if body else None
        if isinstance(parsed, dict) and parsed.get("method") == "server/discover":
            payload = {"jsonrpc": "2.0", "id": parsed.get("id"), "error": {"code": -32601, "message": "Method not found"}}
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": json.dumps(payload).encode()})
            return
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await holder["app"](scope, replay, send)

    return app


@pytest.mark.anyio
async def test_reads_recover_after_the_gateway_restarts():
    """agentgateway forgets every session on restart; switchboard must open a new one by itself."""
    holder = {}
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    web = uvicorn.Server(uvicorn.Config(_legacy_only(holder), log_level="error", lifespan="off"))

    async def serve(app, ready, done):
        async with app.router.lifespan_context(app):
            holder["app"] = app
            ready.set()
            await done.wait()

    async with anyio.create_task_group() as tg:
        tg.start_soon(web.serve, [sock])
        with anyio.fail_after(10):
            while not web.started:
                await anyio.sleep(0.02)
        first_ready, first_done = anyio.Event(), anyio.Event()
        tg.start_soon(serve, _session_server(), first_ready, first_done)
        await first_ready.wait()

        upstream = Upstream("homelab", http_session(f"http://127.0.0.1:{sock.getsockname()[1]}/mcp", "k"))
        upstream.bind(tg)
        assert [t.name for t in await upstream.list_tools()] == ["oura_sleep"]

        first_done.set()  # the "restart": every session id is forgotten
        second_ready, second_done = anyio.Event(), anyio.Event()
        tg.start_soon(serve, _session_server(), second_ready, second_done)
        await second_ready.wait()

        result = await upstream.call_tool("oura_sleep", {}, retry=True)
        assert result.content[0].text == "ok"
        assert [t.name for t in await upstream.list_tools()] == ["oura_sleep"]
        second_done.set()
        web.should_exit = True
        tg.cancel_scope.cancel()
