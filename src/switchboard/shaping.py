"""Make results small: pick the data, drop empties, project with JMESPath, cap, and page the rest."""

from __future__ import annotations

import json
import secrets
import sys
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jmespath
import jmespath.exceptions
import mcp_types as types

_EMPTY = (None, "", [], {})
# A shaped result above this is refused (JSON) or cut (text): a JMESPath query can double
# the serialized size with every stage, and one huge upstream result must not OOM the container.
MAX_RESULT_CHARS = 5_000_000


class QueryError(Exception):
    pass


class ResultTooLarge(QueryError):
    pass


def drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        kept = ((k, drop_empty(v)) for k, v in value.items())
        return {k: v for k, v in kept if v not in _EMPTY}
    if isinstance(value, list):
        return [drop_empty(x) for x in value]  # positions carry meaning (table rows): keep every element
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


def _dumps_capped(data: Any, ceiling: int) -> str:
    """Serialize lazily and stop at the ceiling, so a blown-up projection is never built in full."""
    parts, size = [], 0
    for chunk in json.JSONEncoder(separators=(",", ":"), ensure_ascii=False).iterencode(data):
        size += len(chunk)
        if size > ceiling:
            raise ResultTooLarge(f"result is over {ceiling} chars; narrow it with query")
        parts.append(chunk)
    return "".join(parts)


def render(result: types.CallToolResult, query: str = "", ceiling: int = MAX_RESULT_CHARS) -> tuple[str, list[Any]]:
    data, text, others = pick(result)
    if text is not None:
        if query:
            raise QueryError("query needs a JSON result; this tool returned plain text")
        if len(text) > ceiling:
            text = text[:ceiling] + f"\n…[cut at {ceiling} chars; narrow the request]"
        return text, others
    data = drop_empty(data)
    if query:
        try:
            data = jmespath.search(query, data)
        except jmespath.exceptions.JMESPathError as e:
            raise QueryError(f"bad query: {e}") from None
    return _dumps_capped(data, ceiling), others


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
    scope: str
    expires: float


class ResultCache:
    """Big results kept in memory for paging, per scope; the oldest go first past the byte budget."""

    def __init__(self, ttl_s: float, max_bytes: int, clock: Callable[[], float] = time.monotonic):
        self._ttl, self._max, self._clock = ttl_s, max_bytes, clock
        self._items: OrderedDict[str, Cached] = OrderedDict()
        self._size = 0

    def put(self, text: str, window: int, scope: str) -> str:
        self._expire()
        rid = "r_" + secrets.token_urlsafe(12)
        self._items[rid] = Cached(text, window, scope, self._clock() + self._ttl)
        self._size += sys.getsizeof(text)  # real bytes: CPython stores Cyrillic at 2 per char, emoji at 4
        while self._size > self._max and len(self._items) > 1:
            self._size -= sys.getsizeof(self._items.popitem(last=False)[1].text)
        return rid

    def get(self, rid: str, scope: str) -> Cached | None:
        """A result id only works in the scope that created it."""
        self._expire()
        cached = self._items.get(rid)
        return cached if cached is not None and cached.scope == scope else None

    def _expire(self) -> None:
        now = self._clock()
        for rid in [r for r, c in self._items.items() if c.expires <= now]:
            self._size -= sys.getsizeof(self._items.pop(rid).text)


def window(text: str, offset: int, size: int, rid: str) -> str:
    chunk = text[offset : offset + size]
    end = offset + len(chunk)
    if end < len(text):
        chunk += f'\n…[truncated: {end} of {len(text)} chars — more(result_id="{rid}", offset={end}), or narrow with query]'
    return chunk
