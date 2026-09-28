"""What the model can find: tool metadata, read/write classification, BM25 search, the catalog lines."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import mcp_types as types

from switchboard.config import Server


@dataclass(frozen=True)
class ToolInfo:
    name: str
    server: str
    verb: str  # "read" or "write"
    destructive: bool
    description: str
    summary: str
    input_schema: dict[str, Any]


def split_name(name: str) -> tuple[str, str]:
    """agentgateway names a tool <target>_<tool>; target names never contain '_'."""
    server, sep, tool = name.partition("_")
    return (server, tool) if sep else ("", name)


def classify(tool: types.Tool, servers: dict[str, Server]) -> tuple[str, str, bool]:
    """(server, verb, destructive). Only git config makes a tool read; a write regex or destructive hint wins."""
    server, bare = split_name(tool.name)
    ann = tool.annotations
    destructive = bool(ann and ann.destructive_hint)
    cfg = servers.get(server)
    if cfg is None or destructive or any(p.search(bare) for p in cfg.write):
        return server, "write", destructive
    if any(p.search(bare) for p in cfg.read) or (cfg.trust_annotations and ann is not None and ann.read_only_hint):
        return server, "read", False
    return server, "write", False


def summarize(description: str | None, limit: int = 140) -> str:
    text = " ".join((description or "").split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def _norm(text: str) -> str:
    return re.sub(r"[\s_-]", "", text).lower()


def compact_schema(schema: Any, prop: str | None = None) -> Any:
    """Drop what a model never needs: $schema, closed-object markers, null defaults, empty required, echo titles."""
    if isinstance(schema, list):
        return [compact_schema(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "$schema" or (key == "additionalProperties" and value is False):
            continue
        if (key == "default" and value is None) or (key == "required" and not value):
            continue
        if key == "title" and prop is not None and isinstance(value, str) and _norm(value) == _norm(prop):
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {name: compact_schema(sub, name) for name, sub in value.items()}
        else:
            out[key] = compact_schema(value)
    return out


_STOP = frozenset(["a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "its", "of", "on", "or", "that", "the", "this", "to", "with"])
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _CAMEL.sub(" ", text).lower()) if t and t not in _STOP]


class Index:
    """BM25 over server name, server about, tool name and description."""

    def __init__(self, tools: list[ToolInfo], servers: dict[str, Server], k1: float = 1.5, b: float = 0.75):
        self.tools, self.k1, self.b = tools, k1, b
        about = {name: s.about for name, s in servers.items()}
        self.docs = [Counter(tokens(f"{t.server} {about.get(t.server, '')} {t.name} {t.description}")) for t in tools]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lengths) / len(self.lengths) if tools else 0.0
        df = Counter(term for d in self.docs for term in d)
        n = len(tools)
        self.idf = {term: math.log(1 + (n - f + 0.5) / (f + 0.5)) for term, f in df.items()}

    def search(self, query: str, server: str = "", limit: int = 8) -> list[ToolInfo]:
        pool = [i for i, t in enumerate(self.tools) if not server or t.server == server]
        terms = tokens(query)
        if not terms:
            return [self.tools[i] for i in pool][:limit] if server else []
        scored = []
        for i in pool:
            doc = self.docs[i]
            norm = self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avg)
            score = sum(self.idf[t] * doc[t] * (self.k1 + 1) / (doc[t] + norm) for t in terms if t in doc)
            if score > 0:
                scored.append((-score, self.tools[i].name, i))
        return [self.tools[i] for _, _, i in sorted(scored)[:limit]]


def build(tools: list[types.Tool], servers: dict[str, Server]) -> list[ToolInfo]:
    out = []
    for t in tools:
        server, verb, destructive = classify(t, servers)
        out.append(
            ToolInfo(
                name=t.name,
                server=server,
                verb=verb,
                destructive=destructive,
                description=t.description or "",
                summary=summarize(t.description),
                input_schema=dict(t.input_schema or {}),
            )
        )
    return out


def render_catalog(counts: dict[str, int] | None, servers: dict[str, Server]) -> str:
    """One line per server: the scope's own servers with counts, or every configured one without."""
    if counts is None:
        return "\n".join(f"- {name}: {s.about}" for name, s in sorted(servers.items()))
    return "\n".join(
        f"- {name} ({n} tools)" + (f": {servers[name].about}" if name in servers else "")
        for name, n in sorted(counts.items())
    )
