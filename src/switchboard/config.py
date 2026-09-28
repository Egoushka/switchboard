"""Load and validate switchboard's YAML config. Secret values come from env vars named in the file."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml

DEFAULT_APPROVAL_TIMEOUT_S = 50.0


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Approval:
    telegram_token: str
    approver_id: int
    timeout_s: float


@dataclass(frozen=True)
class Results:
    max_chars: int = 6000
    hard_max_chars: int = 40000
    cache_ttl_s: float = 600
    cache_max_bytes: int = 32_000_000  # real bytes; well below the 192m container limit


@dataclass(frozen=True)
class Scope:
    name: str
    upstream_route: str
    key: str
    writes: bool
    approval_timeout_s: float


@dataclass(frozen=True)
class Server:
    about: str
    read: tuple[re.Pattern[str], ...] = ()
    write: tuple[re.Pattern[str], ...] = ()
    trust_annotations: bool = False


@dataclass(frozen=True)
class Config:
    upstream: str
    ingress_token: str
    approval: Approval | None
    results: Results
    scopes: dict[str, Scope]
    servers: dict[str, Server]


def _check(where: str, value: Any, allowed: set[str], required: frozenset[str] = frozenset()) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{where}: expected a mapping")
    if unknown := set(value) - allowed:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
    if missing := required - set(value):
        raise ConfigError(f"{where}: missing key(s) {sorted(missing)}")
    return value


def _env(env: Mapping[str, str], name: str, where: str) -> str:
    if not (value := env.get(name, "")):
        raise ConfigError(f"{where}: env var {name} is not set")
    return value


def _patterns(where: str, values: Any) -> tuple[re.Pattern[str], ...]:
    """A list of regex strings. A bare string would compile per character, and "^" alone matches everything."""
    if values is None:
        return ()
    if not isinstance(values, list) or not all(isinstance(p, str) for p in values):
        raise ConfigError(f"{where}: must be a list of regex strings")
    try:
        return tuple(re.compile(p) for p in values)
    except re.error as e:
        raise ConfigError(f"{where}: {e}") from None


def load(path: str, env: Mapping[str, str] = os.environ) -> Config:
    with open(path) as f:
        raw = _check(
            "config",
            yaml.safe_load(f) or {},
            {"upstream", "ingress_token_env", "approval", "results", "scopes", "servers"},
            frozenset({"upstream", "ingress_token_env", "scopes"}),
        )

    results = Results(
        **_check("results", raw.get("results") or {}, {"max_chars", "hard_max_chars", "cache_ttl_s", "cache_max_bytes"})
    )

    approval_raw = raw.get("approval")
    default_timeout = float((approval_raw or {}).get("timeout_s", DEFAULT_APPROVAL_TIMEOUT_S))
    if not isinstance(raw["scopes"], Mapping) or not raw["scopes"]:
        raise ConfigError("scopes: at least one scope is required")
    scopes: dict[str, Scope] = {}
    for name, s in raw["scopes"].items():
        where = f"scopes.{name}"
        _check(
            where,
            s,
            {"upstream_route", "key_env", "writes", "approval_timeout_s"},
            frozenset({"upstream_route", "key_env", "writes"}),
        )
        writes = "off" if s["writes"] is False else s["writes"]  # YAML 1.1 reads a bare `off` as False
        if writes not in ("approve", "off"):
            raise ConfigError(f"{where}.writes: must be 'approve' or 'off', got {s['writes']!r}")
        scopes[name] = Scope(
            name=name,
            upstream_route=s["upstream_route"],
            key=_env(env, s["key_env"], where),
            writes=writes == "approve",
            approval_timeout_s=float(s.get("approval_timeout_s", default_timeout)),
        )

    approval = None
    if any(s.writes for s in scopes.values()):
        if approval_raw is None:
            raise ConfigError("approval: required when a scope has writes: approve")
        _check(
            "approval",
            approval_raw,
            {"telegram_token_env", "approver_id_env", "timeout_s"},
            frozenset({"telegram_token_env", "approver_id_env"}),
        )
        approver = _env(env, approval_raw["approver_id_env"], "approval")
        if not approver.isdigit():
            raise ConfigError("approval: the approver id must be a Telegram user id (digits only)")
        approval = Approval(
            telegram_token=_env(env, approval_raw["telegram_token_env"], "approval"),
            approver_id=int(approver),
            timeout_s=default_timeout,
        )

    servers: dict[str, Server] = {}
    for name, s in (raw.get("servers") or {}).items():
        where = f"servers.{name}"
        _check(where, s, {"about", "read", "write", "trust_annotations"}, frozenset({"about"}))
        trust = s.get("trust_annotations", False)
        if not isinstance(trust, bool):  # bool("false") is True
            raise ConfigError(f"{where}.trust_annotations: must be true or false")
        servers[name] = Server(
            about=s["about"],
            read=_patterns(f"{where}.read", s.get("read")),
            write=_patterns(f"{where}.write", s.get("write")),
            trust_annotations=trust,
        )

    return Config(
        upstream=raw["upstream"].rstrip("/"),
        ingress_token=_env(env, raw["ingress_token_env"], "config"),
        approval=approval,
        results=results,
        scopes=scopes,
        servers=servers,
    )
