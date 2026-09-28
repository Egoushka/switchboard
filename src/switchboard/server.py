"""The five tools, one MCP app per scope, behind a bearer only agentgateway holds."""

from __future__ import annotations

import contextlib
import functools
import hashlib
import hmac
import json
import logging
import time
from typing import Any

import anyio
import jmespath
import jmespath.exceptions
import mcp_types as types
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from switchboard import metrics
from switchboard.approval import ApprovalUnavailable, Approver
from switchboard.catalog import ToolInfo, compact_schema, render_catalog
from switchboard.config import Config, Scope
from switchboard.shaping import QueryError, ResultCache, render, upstream_chars, window
from switchboard.upstream import ToolCache, Upstream, UpstreamError

log = logging.getLogger(__name__)
audit = logging.getLogger("switchboard.audit")

INSTRUCTIONS = (
    "Yehor's homelab tools sit behind these tools. Find one with search, read its arguments with describe, "
    "then call it with read (no side effects) or write (waits for Yehor to approve it on his phone). "
    "Narrow big JSON results with query (JMESPath); page the rest with more."
)
# An approved write runs shielded from the client going away, but never longer than this.
WRITE_TIMEOUT_S = 300
READ_ONLY = types.ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITE = types.ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)


class Refusal(Exception):
    """Ends a call with an error result the model can act on."""


def ok(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)])


def guarded(fn):
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> types.CallToolResult:
        try:
            return await fn(*args, **kwargs)
        except (Refusal, QueryError, UpstreamError) as e:
            return types.CallToolResult(content=[types.TextContent(type="text", text=str(e))], is_error=True)

    return wrapper


class ScopeTools:
    def __init__(
        self,
        cfg: Config,
        scope: Scope,
        tools: ToolCache,
        upstream: Upstream,
        results: ResultCache,
        approver: Approver | None,
    ):
        self.cfg, self.scope, self.tools, self.upstream = cfg, scope, tools, upstream
        self.results, self.approver = results, approver

    async def lookup(self, name: str) -> ToolInfo:
        tools, index = await self.tools.get()
        if (tool := tools.get(name)) is None:
            hint = ", ".join(t.name for t in index.search(name, limit=3))
            raise Refusal(f"unknown tool {name!r}" + (f"; did you mean: {hint}" if hint else ""))
        return tool

    def shape(self, tool: ToolInfo, result: types.CallToolResult, query: str, max_chars: int | None) -> types.CallToolResult:
        text, extra = render(result, query)
        limits = self.cfg.results
        size = limits.max_chars if max_chars is None else max(1, min(max_chars, limits.hard_max_chars))
        if len(text) > size:
            text = window(text, 0, size, self.results.put(text, size, self.scope.name))
        metrics.result_chars.labels(self.scope.name, tool.server, "upstream").inc(upstream_chars(result))
        metrics.result_chars.labels(self.scope.name, tool.server, "returned").inc(len(text))
        return types.CallToolResult(content=[types.TextContent(type="text", text=text), *extra], is_error=result.is_error)

    async def search(self, query: str = "", server: str = "", limit: int = 8) -> types.CallToolResult:
        tools, index = await self.tools.get()
        hits = index.search(query, server=server, limit=max(1, min(limit, 20)))
        if not hits:
            return ok("No matching tool. Servers: " + ", ".join(sorted({t.server for t in tools.values()})))
        return ok("\n".join(f"{t.name} [{t.verb}] — {t.summary}" for t in hits))

    async def describe(self, tools: list[str]) -> types.CallToolResult:
        found: list[dict[str, Any]] = []
        for name in tools[:5]:
            try:
                t = await self.lookup(name)
            except Refusal as e:
                found.append({"tool": name, "error": str(e)})
                continue
            found.append(
                {"tool": t.name, "verb": t.verb, "description": t.description, "input_schema": compact_schema(t.input_schema)}
            )
        return ok(json.dumps(found, separators=(",", ":"), ensure_ascii=False))

    async def read(
        self, tool: str, args: dict[str, Any] | None = None, query: str = "", max_chars: int | None = None
    ) -> types.CallToolResult:
        t = await self.lookup(tool)
        if t.verb != "read":
            raise Refusal(f"{tool} is a write tool; use write(tool, args, reason)")
        result = await self.upstream.call_tool(t.name, args or {}, retry=True)
        metrics.calls.labels(self.scope.name, t.server, "read", "error" if result.is_error else "ok").inc()
        return self.shape(t, result, query, max_chars)

    async def write(
        self,
        tool: str,
        reason: str,
        ctx: Context,
        args: dict[str, Any] | None = None,
        query: str = "",
        max_chars: int | None = None,
    ) -> types.CallToolResult:
        if self.approver is None or not self.scope.writes:
            raise Refusal("this scope is read-only")
        t = await self.lookup(tool)
        if query:  # a query that cannot work must fail before Yehor is asked, not after the write ran
            try:
                jmespath.compile(query)
            except jmespath.exceptions.JMESPathError as e:
                raise Refusal(f"bad query: {e}; nothing was executed") from None
        args = args or {}
        client = (ctx.headers or {}).get("x-switchboard-client") or self.scope.name
        started = time.monotonic()
        record = {
            "event": "write",
            "scope": self.scope.name,
            "client": client,
            "tool": t.name,
            "args_sha256": hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "reason": reason[:200],
        }

        async def progress() -> None:
            await ctx.report_progress(time.monotonic() - started, self.scope.approval_timeout_s, "waiting for Yehor's approval")

        try:
            req = await self.approver.ask(
                scope=self.scope.name, client=client, tool=t.name, args=args, reason=reason,
                destructive=t.destructive, timeout_s=self.scope.approval_timeout_s, on_wait=progress,
            )
        except ApprovalUnavailable as e:
            self._audit(record, "unavailable", "-", started)
            raise Refusal(f"approval unavailable ({e}); nothing was executed") from None
        record["approval_id"] = req.id
        metrics.approvals.labels(self.scope.name, req.decision).inc()
        metrics.approval_wait.observe(time.monotonic() - started)
        if req.decision != "approved":
            self._audit(record, req.decision, "-", started)
            hint = " — ask Yehor to approve, then call write again" if req.decision == "expired" else ""
            raise Refusal(f"{req.decision}; nothing was executed{hint}")

        # Approved. The decision is on record before the upstream call (so it survives a kill), and the
        # call is shielded: a client giving up must not cut an approved write half-way.
        self._audit(record, "approved", "running", started)
        result: types.CallToolResult | None = None
        failure = "the upstream did not answer"
        with anyio.CancelScope(shield=True):
            try:
                with anyio.fail_after(WRITE_TIMEOUT_S):
                    result = await self.upstream.call_tool(t.name, req.args, retry=False)
            except UpstreamError as e:
                failure = str(e)
            except TimeoutError:
                failure = f"the upstream did not answer in {WRITE_TIMEOUT_S} s"
            outcome = "unknown" if result is None else ("error" if result.is_error else "ok")
            self._audit(record, "approved", outcome, started)
            with anyio.move_on_after(15):
                shown = {"ok": "ok", "error": "upstream error", "unknown": f"result unknown: {failure}"}[outcome]
                await self.approver.report(req, f"✅ approved — {shown}")
        if result is None:
            raise Refusal(f"approved, but the result is unknown: {failure}; the write may or may not have happened")
        metrics.calls.labels(self.scope.name, t.server, "write", outcome).inc()
        try:
            return self.shape(t, result, query, max_chars)
        except QueryError as e:  # the write ran: say so, instead of an error the model would retry
            shaped = self.shape(t, result, "", max_chars)
            note = types.TextContent(type="text", text=f"[the write ran; query not applied: {e}]")
            return types.CallToolResult(content=[note, *shaped.content], is_error=shaped.is_error)

    def _audit(self, record: dict[str, Any], decision: str, outcome: str, started: float) -> None:
        line = {
            **record,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "decision": decision,
            "wait_ms": int((time.monotonic() - started) * 1000),
            "outcome": outcome,
        }
        audit.info(json.dumps(line, ensure_ascii=False))

    async def more(self, result_id: str, offset: int) -> types.CallToolResult:
        cached = self.results.get(result_id, self.scope.name)
        if cached is None:
            raise Refusal("result expired; run the tool again")
        if offset >= len(cached.text):
            return ok(f"[end of result: {len(cached.text)} chars]")
        return ok(window(cached.text, max(0, offset), cached.window, result_id))


def descriptions(catalog: str) -> dict[str, str]:
    return {
        "search": (
            "Find one of Yehor's homelab tools. Returns lines '<tool> [read|write] — <summary>'. "
            "An empty query with a server lists that server's tools.\nServers:\n" + catalog
        ),
        "describe": "Full description and input schema for up to 5 tools found with search.",
        "read": (
            "Run a [read] tool: tool is its name from search, args its arguments. query: JMESPath applied to a JSON "
            "result, e.g. 'items[].name'. max_chars: size of the first page (default 6000); page the rest with more."
        ),
        "write": (
            "Run a [write] tool. Waits for Yehor to approve it on his phone; reason is shown to him. Returns denied or "
            "expired if he does not approve, and nothing runs. Same query and max_chars as read."
        ),
        "more": "The next page of a truncated result: the result_id and offset from its trailer.",
    }


def register(server: MCPServer, tools: ScopeTools, catalog: str) -> None:
    d = descriptions(catalog)
    server.add_tool(guarded(tools.search), name="search", description=d["search"], annotations=READ_ONLY)
    server.add_tool(guarded(tools.describe), name="describe", description=d["describe"], annotations=READ_ONLY)
    server.add_tool(guarded(tools.read), name="read", description=d["read"], annotations=READ_ONLY)
    if tools.scope.writes and tools.approver is not None:
        server.add_tool(guarded(tools.write), name="write", description=d["write"], annotations=WRITE)
    server.add_tool(guarded(tools.more), name="more", description=d["more"], annotations=READ_ONLY)


class BearerAuth:
    """Every path but /healthz needs the bearer agentgateway sends (backendAuth)."""

    def __init__(self, app: Any, token: str):
        self.app, self.expected = app, f"Bearer {token}".encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"] != "/healthz":
            got = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(got, self.expected):
                await PlainTextResponse("unauthorized", status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def healthz(_request: Any) -> PlainTextResponse:
    return PlainTextResponse("ok")


def build_app(cfg: Config, upstreams: dict[str, Upstream], approver: Approver | None) -> BearerAuth:
    results = ResultCache(cfg.results.cache_ttl_s, cfg.results.cache_max_bytes)
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    mcp_servers = {name: MCPServer(f"switchboard-{name}", instructions=INSTRUCTIONS) for name in cfg.scopes}
    apps = {
        name: server.streamable_http_app(streamable_http_path=f"/mcp/{name}", transport_security=security, host="0.0.0.0")
        for name, server in mcp_servers.items()
    }
    caches = {name: ToolCache(name, upstreams[name], cfg.servers) for name in cfg.scopes}

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        async with anyio.create_task_group() as tg:
            for upstream in upstreams.values():
                upstream.bind(tg)
            if approver is not None:
                tg.start_soon(approver.poll_forever)
            for name, scope in cfg.scopes.items():
                counts = None
                with anyio.move_on_after(20):  # agentgateway may still be starting: fall back to config lines
                    counts = await caches[name].counts()
                tools = ScopeTools(cfg, scope, caches[name], upstreams[name], results, approver)
                register(mcp_servers[name], tools, render_catalog(counts, cfg.servers))
            async with contextlib.AsyncExitStack() as stack:
                for app in apps.values():
                    await stack.enter_async_context(app.router.lifespan_context(app))
                yield
            tg.cancel_scope.cancel()

    routes = [Route("/healthz", healthz), *(route for app in apps.values() for route in app.routes)]
    return BearerAuth(Starlette(routes=routes, lifespan=lifespan), cfg.ingress_token)
