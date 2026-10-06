---
title: "Overview"
description: "What switchboard is, the tools an agent sees through it, which tools may write, what it leaves to the gateway, and where it stands at v0.1.0."
order: 0
section: "Get started"
---

switchboard is an MCP server that sits between your agents and an MCP gateway. An agent connected straight to a gateway route receives the definition of every tool behind it before it starts on the task. An agent connected through switchboard receives four small tools, or five where you allow writes, and looks up the one it needs. No call to `write` runs until a person taps Approve on a Telegram message.

It is built to sit behind [agentgateway](https://agentgateway.dev), which keeps the client keys, the allow-lists and the upstream credentials. switchboard accepts one bearer: the one the gateway sends ([README](../../README.md), `BearerAuth` in [server.py](../../src/switchboard/server.py)).

## Who it is for

You run agentgateway in front of several MCP servers. Your agents pay for the whole tool list in every session, and you want a person to approve each call that changes something.

switchboard was written for one operator's own gateway. It has one approver per process, and its server instructions and tool descriptions are fixed text written for that setup, with no config key to change them (`INSTRUCTIONS` and `descriptions` in [server.py](../../src/switchboard/server.py)).

## The tools

| tool | what it does | offered in |
|---|---|---|
| `search` | ranks tools by words, or lists one server's tools | every scope |
| `describe` | description and input schema of up to 5 tools | every scope |
| `read` | runs a tool your config marks as read | every scope |
| `write` | runs a tool after a person approves the call | scopes with `writes: approve` |
| `more` | the next page of a result that was cut | every scope |

A scope with `writes: off` lists four tools. [test_integration.py](../../tests/test_integration.py) checks both lists (`test_read_only_scope_lists_four_small_tools_with_the_catalog`, `test_writable_scope_adds_write`). The [reference](reference.md#tools) has every argument.

## How it works

Each *scope* in the config is an MCP endpoint at `/mcp/<scope>` plus one long-lived MCP session to a gateway route, whose tools the gateway has already merged and named `<target>_<tool>`. `search` ranks those tools with BM25 over the server name, the server's `about` line, the tool name and the description. `read` runs a tool your config marks as read. `write` sends the tool name, the arguments and the agent's `reason` to Telegram, and runs the call once if the approver taps Approve before the timeout, 50 seconds by default. Both return the result trimmed: empty values dropped, an optional JMESPath `query` applied, the first page cut at 6,000 characters by default, the rest behind `more`. [How it works](how-it-works.md) follows a request through each part.

## Which tools may write

By default switchboard does not take a tool's word that it only reads. A tool is `read` only if your config says so: a `read:` regex on its server, or `trust_annotations: true` on its server and `readOnlyHint` on the tool. A server with no entry is all `write`, and a `write:` regex or a `destructiveHint` always makes a tool `write` (`classify` in [catalog.py](../../src/switchboard/catalog.py), [test_catalog.py](../../tests/test_catalog.py)).

## What it does not do

- It does not check client keys. Per-client keys and allow-lists belong to the gateway; switchboard sees one bearer.
- It does not merge MCP servers. Each scope reads one gateway route.
- It serves MCP over Streamable HTTP only. There is no stdio mode (`build_app` in [server.py](../../src/switchboard/server.py)).
- It reads its config once, at start ([`__main__.py`](../../src/switchboard/__main__.py)). To change it, restart the process.
- It keeps nothing on disk. Pending approvals, cut results and the tool catalog live in memory.
- It has one approval channel, a Telegram bot, and one approver (`Approval` in [config.py](../../src/switchboard/config.py)).

## Current status

- **v0.1.0**, the only tag. This guide describes `main` at that tag ([pyproject.toml](../../pyproject.toml)).
- **A container image, no package.** CI pushes `ghcr.io/egoushka/switchboard:<version>` for each `v*` tag ([ci.yml](../../.github/workflows/ci.yml)). There is no PyPI package and no GitHub release.
- **91 tests, all offline.** They stand in for the gateway with an in-process MCP server and for Telegram with a fake. A smoke test runs the image behind a real agentgateway with a fake Telegram, writes included; CI runs it on every push.

> [!WARNING]
> The name `switchboard` on PyPI belongs to an unrelated project. `pip install switchboard` does not install this one; use the container image or a clone.

[Status and evidence](status.md) lists what works, what is partial and what does not exist, with the test behind each.

## Where to go next

- [Quickstart](quickstart.md): the image between two agentgateway routes and a fake MCP server, checked by a script.
- [Run it behind agentgateway](deploy.md): your config, the container, and the route clients use.
- [Approve writes on your phone](approvals.md): the Telegram bot, what the approver sees, and the audit log.
- [How it works](how-it-works.md): search, read or write, results, the upstream session, and an approved write step by step.
- [Reference](reference.md): tools, errors, config keys, environment variables, routes, metrics and limits.
- [Status and evidence](status.md): each capability and the test behind it.
