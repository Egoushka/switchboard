"""Make results small: pick the data, drop empties, project with JMESPath, cap, and page the rest."""

from __future__ import annotations

import json
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jmespath
import jmespath.exceptions
import mcp_types as types

_EMPTY = (None, "", [], {})


class QueryError(Exception):
    pass


def drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        kept = ((k, drop_empty(v)) for k, v in value.items())
        return {k: v for k, v in kept if v not in _EMPTY}
    if isinstance(value, list):
        return [v for v in (drop_empty(x) for x in value) if v not in _EMPTY]
    return value


def pick(result: types.CallToolResult) -> tuple[Any, str | None, list[Any]]:
    """(data, text, other blocks). data is the JSON when there is some; otherwise text holds the plain text."""
    texts = [b.text for b in result.content if isinstance(b, types.TextContent)]
    others = [b for b in result.content if not isinstance(b, types.TextContent)]
    if result.structured_content is not None:
        return result.structured_content, None, others
    if len(texts) == 1:
        try:
            return json.loads(texts[0]), None, others
        except ValueError:
            pass
    return None, "\n".join(texts), others


def render(result: types.CallToolResult, query: str = "") -> tuple[str, list[Any]]:
    data, text, others = pick(result)
    if text is not None:
        if query:
            raise QueryError("query needs a JSON result; this tool returned plain text")
        return text, others
    data = drop_empty(data)
    if query:
        try:
            data = jmespath.search(query, data)
        except jmespath.exceptions.JMESPathError as e:
            raise QueryError(f"bad query: {e}") from None
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False), others


def upstream_chars(result: types.CallToolResult) -> int:
    """What a direct client would have received as text: every text block plus the structured copy."""
    n = sum(len(b.text) for b in result.content if isinstance(b, types.TextContent))
    if result.structured_content is not None:
        n += len(json.dumps(result.structured_content, separators=(",", ":"), ensure_ascii=False))
    return n


@dataclass
class Cached:
    text: str
    window: int
    expires: float


class ResultCache:
    """Big results kept in memory for paging; the oldest go first past the budget."""

    def __init__(self, ttl_s: float, max_bytes: int, clock: Callable[[], float] = time.monotonic):
        self._ttl, self._max, self._clock = ttl_s, max_bytes, clock
        self._items: OrderedDict[str, Cached] = OrderedDict()
        self._size = 0

    def put(self, text: str, window: int) -> str:
        self._expire()
        rid = "r_" + secrets.token_urlsafe(12)
        self._items[rid] = Cached(text, window, self._clock() + self._ttl)
        # ponytail: counts chars, not bytes; close enough for a memory budget.
        self._size += len(text)
        while self._size > self._max and len(self._items) > 1:
            self._size -= len(self._items.popitem(last=False)[1].text)
        return rid

    def get(self, rid: str) -> Cached | None:
        self._expire()
        return self._items.get(rid)

    def _expire(self) -> None:
        now = self._clock()
        for rid in [r for r, c in self._items.items() if c.expires <= now]:
            self._size -= len(self._items.pop(rid).text)


def window(text: str, offset: int, size: int, rid: str) -> str:
    chunk = text[offset : offset + size]
    end = offset + len(chunk)
    if end < len(text):
        chunk += f'\n…[truncated: {end} of {len(text)} chars — more(result_id="{rid}", offset={end}), or narrow with query]'
    return chunk
