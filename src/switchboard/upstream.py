"""One long-lived MCP session per scope to its agentgateway route, and the scope's tool cache."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, TypeVar

import anyio
import mcp_types as types
from anyio.abc import TaskGroup, TaskStatus
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.exceptions import MCPError

from switchboard import metrics
from switchboard.catalog import Index, ToolInfo, build
from switchboard.config import Server

log = logging.getLogger(__name__)
T = TypeVar("T")
# What the SDK raises when the session itself is gone: a torn-down connection (-32000), or a
# session id the server no longer knows (-32600, "Session not found" after an agentgateway
# restart). Any other code is the server's answer, and the session is fine.
SESSION_LOST = frozenset({types.CONNECTION_CLOSED, types.INVALID_REQUEST})
Connect = Callable[[], AbstractAsyncContextManager[Client]]


class UpstreamError(Exception):
    pass


def http_session(url: str, key: str) -> Connect:
    @asynccontextmanager
    async def connect():
        async with (
            create_mcp_http_client(headers={"Authorization": f"Bearer {key}"}) as http,
            Client(streamable_http_client(url, http_client=http)) as client,
        ):
            yield client

    return connect


class Upstream:
    """One MCP session owned by its own task: the SDK's context managers must enter and exit in one task."""

    def __init__(self, label: str, connect: Connect):
        self.label, self._connect = label, connect
        self._client: Client | None = None
        self._stop: anyio.Event | None = None
        self._lock = anyio.Lock()
        self._tg: TaskGroup | None = None

    def bind(self, tg: TaskGroup) -> None:
        self._tg = tg

    async def _own(self, *, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED) -> None:
        stop, started = anyio.Event(), False
        try:
            async with self._connect() as client:
                self._client, self._stop, started = client, stop, True
                task_status.started()
                await stop.wait()
        except Exception:
            if not started:
                raise
            log.warning("upstream %s: session ended", self.label, exc_info=True)
        finally:
            if self._stop is stop:
                self._client = self._stop = None

    async def _session(self) -> Client:
        async with self._lock:
            if self._client is None:
                if self._tg is None:
                    raise RuntimeError("Upstream.bind() was not called")
                try:
                    await self._tg.start(self._own)
                except Exception as e:
                    metrics.upstream_errors.labels(self.label).inc()
                    raise UpstreamError(f"upstream {self.label} unavailable") from e
            assert self._client is not None
            return self._client

    def _drop(self, client: Client) -> None:
        """Stop the session that failed, never a newer one another request has already opened."""
        if self._client is not client:
            return
        if self._stop is not None:
            self._stop.set()
        self._client = self._stop = None

    async def _request(self, op: Callable[[Client], Awaitable[T]], retry: bool) -> T:
        for attempt in (1, 2):
            client = await self._session()
            try:
                return await op(client)
            except MCPError as e:
                if e.code not in SESSION_LOST:  # the server answered with an error: the session is fine
                    raise UpstreamError(f"{self.label}: {e.message}") from None
                failure: Exception = e
            except Exception as e:  # noqa: BLE001 - any transport failure ends this session
                failure = e
            self._drop(client)
            metrics.upstream_errors.labels(self.label).inc()
            if not retry or attempt == 2:
                raise UpstreamError(f"upstream {self.label} unavailable") from failure
        raise AssertionError("unreachable")

    async def list_tools(self) -> list[types.Tool]:
        async def op(client: Client) -> list[types.Tool]:
            tools: list[types.Tool] = []
            cursor = None
            while True:
                page = await client.list_tools(cursor=cursor)
                tools += page.tools
                if not (cursor := page.next_cursor):
                    return tools

        return await self._request(op, retry=True)

    async def call_tool(self, name: str, args: dict[str, Any], retry: bool) -> types.CallToolResult:
        return await self._request(lambda c: c.call_tool(name, args, read_timeout_seconds=300), retry)


class ToolCache:
    """A scope's catalog, refreshed at most every ttl_s; a failed refresh keeps serving the last good one."""

    def __init__(
        self,
        scope: str,
        upstream: Upstream,
        servers: dict[str, Server],
        ttl_s: float = 300,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.scope, self._upstream, self._servers, self._ttl, self._clock = scope, upstream, servers, ttl_s, clock
        self._tools: dict[str, ToolInfo] = {}
        self._index: Index | None = None
        self._at = -math.inf
        self._lock = anyio.Lock()

    async def get(self) -> tuple[dict[str, ToolInfo], Index]:
        async with self._lock:
            if self._index is None or self._clock() - self._at >= self._ttl:
                try:
                    infos = build(await self._upstream.list_tools(), self._servers)
                except UpstreamError:
                    if self._index is None:
                        raise
                    log.warning("scope %s: catalog refresh failed, serving the last one", self.scope)
                    self._at = self._clock()
                else:
                    self._tools = {t.name: t for t in infos}
                    self._index = Index(infos, self._servers)
                    self._at = self._clock()
                    metrics.catalog_tools.labels(self.scope).set(len(infos))
            return self._tools, self._index

    async def counts(self) -> dict[str, int] | None:
        try:
            tools, _ = await self.get()
        except UpstreamError:
            return None
        out: dict[str, int] = {}
        for t in tools.values():
            out[t.server] = out.get(t.server, 0) + 1
        return out
