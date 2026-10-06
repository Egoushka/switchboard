---
title: "Quickstart"
description: "Build the image, start switchboard between two agentgateway routes and a fake MCP server, and call search and read through the gateway."
order: 1
section: "Get started"
---

You run the repository's smoke test: switchboard in a container between two agentgateway routes, a fake MCP server behind it, and a script that connects as a client. You need no real MCP server, no Telegram bot and no API key: a fake MCP server and a fake Telegram Bot API stand in. One route is read-only; the other allows writes and a fake approver taps Approve or Deny.

## Prerequisites

- Docker with Compose. The build pulls `python:3.13-slim` and uv 0.11 ([Dockerfile](../../Dockerfile)); the stack pulls agentgateway v1.5.0, pinned by digest in [compose.yaml](../../tests/smoke/compose.yaml).
- [uv](https://docs.astral.sh/uv/). The project needs Python 3.13 or later ([pyproject.toml](../../pyproject.toml), [.python-version](../../.python-version)).
- git.

## Get the code and run the tests

```bash
git clone https://github.com/Egoushka/switchboard.git
cd switchboard
uv sync
uv run pytest
```

pytest reports `91 passed`. The tests call no network service outside 127.0.0.1.

## Start the stack

```bash title="Build the image and start the smoke stack"
docker build -t switchboard:dev .
docker compose -f tests/smoke/compose.yaml up -d
```

[compose.yaml](../../tests/smoke/compose.yaml) starts four containers:

| service | what it runs | its config |
|---|---|---|
| `upstream` | a fake MCP server with a read tool `sleep`, a read tool `added` and a write tool `add` | [upstream.py](../../tests/smoke/upstream.py) |
| `telegram` | a fake Telegram Bot API that taps Approve, or Deny when the request shows `deny-me`, on `127.0.0.1:18081` | [telegram.py](../../tests/smoke/telegram.py) |
| `agentgateway` | agentgateway, published on `127.0.0.1:13000` | [agw.yaml](../../tests/smoke/agw.yaml) |
| `switchboard` | the image you built, with two scopes, `homelab-ro` and `homelab-rw`, and `SWITCHBOARD_TELEGRAM_API` pointing at the fake | [switchboard.yaml](../../tests/smoke/switchboard.yaml) |

## Call it through the gateway

```bash
uv run python tests/smoke/check.py
```

It prints `smoke OK`. It waits up to 90 seconds for the stack to answer, because switchboard starts accepting requests only after it has tried to fetch the gateway's tool list, waiting at most 20 seconds per scope (`lifespan` in [server.py](../../src/switchboard/server.py)). CI runs the same check on every push ([ci.yml](../../.github/workflows/ci.yml)).

[check.py](../../tests/smoke/check.py) connects to `http://127.0.0.1:13000/mcp/lab-ro` with the bearer `smoke-client-key` and asserts, on the read-only route:

1. `tools/list` returns `lab_search`, `lab_describe`, `lab_read` and `lab_more`.
2. `lab_search` with `{"query": "sleep"}` returns a first line that starts `oura_sleep [read]`.
3. `lab_read` with `{"tool": "oura_sleep"}` returns `{"day":"2026-09-27","score":81}`.
4. `lab_write` is refused.

On `/mcp/lab-rw` with the bearer `smoke-writer-key`, it asserts:

1. `tools/list` also returns `lab_write`.
2. A `lab_write` of `oura_add` with the text `approve-me` runs once: `oura_added` then returns it.
3. A `lab_write` with the text `deny-me` returns `denied; nothing was executed`, and `oura_added` still returns one text.
4. The fake Telegram received two messages, both naming the client `smoke-writer` that the gateway set (not the spoofed header the script sends), and edited them to show `approved — ok` and `denied`.

The fake tool also returns `"contributors": null`. switchboard dropped it.

## What happened

The request crossed the gateway twice:

```text
check.py
  |  Authorization: Bearer smoke-client-key
  v
agentgateway   route lab-ro       /mcp/lab-ro
  |  Authorization: Bearer smoke-token          (backendAuth)
  v
switchboard    scope homelab-ro   /mcp/homelab-ro
  |  Authorization: Bearer smoke-upstream-key   (key_env)
  v
agentgateway   route homelab      /mcp/homelab
  |
  v
upstream       tool sleep, behind target oura, named oura_sleep
```

- The `homelab` route has one target, `oura`, and `prefixMode: always`, so the gateway names the fake tool `oura_sleep`. switchboard reads the part before the first `_` as the server.
- [switchboard.yaml](../../tests/smoke/switchboard.yaml) gives server `oura` `trust_annotations: true`, and the fake tool sets `readOnlyHint`, so `oura_sleep` is a read tool.
- The `lab-ro` route treats switchboard as one more MCP server, target `lab`. That is why the client sees `lab_search` and not `search`.
- switchboard accepts the request because the gateway sends `smoke-token`: the value of `SWITCHBOARD_TOKEN`, the variable that `ingress_token_env` names.
- The `lab-ro` route also sets the `x-switchboard-client` header from the client key's metadata. switchboard puts that name in approval messages and audit lines; on the `lab-rw` route it is `smoke-writer`, and check.py reads it in the fake Telegram's messages.

## Try other calls

To connect another MCP client to the stack, give it these settings:

| setting | value |
|---|---|
| transport | Streamable HTTP |
| URL | `http://127.0.0.1:13000/mcp/lab-ro` |
| header | `Authorization: Bearer smoke-client-key` |

Then try these calls:

| call | returns |
|---|---|
| `lab_search` with `{"server": "oura"}` | that server's tools, up to `limit` (8) |
| `lab_search` with `{}` | `No matching tool. Servers: oura` |
| `lab_describe` with `{"tools": ["oura_sleep"]}` | the tool's description and input schema |
| `lab_read` with `{"tool": "oura_sleep", "query": "score"}` | `81` |
| `lab_read` with `{"tool": "oura_slep"}` | an error that suggests `oura_sleep` |

## Stop it

```bash
docker compose -f tests/smoke/compose.yaml down
```

Next, [run it behind your own gateway](deploy.md), then [let agents write with your approval](approvals.md).
