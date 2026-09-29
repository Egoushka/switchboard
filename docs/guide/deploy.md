---
title: "Run it behind agentgateway"
description: "Write the config for your gateway routes, mark the tools that read, run the image with its secrets, and send clients to it through agentgateway."
order: 2
section: "Guides"
---

You write a config that maps each scope to a gateway route, run the container with the secrets the config names, and add a gateway route that sends clients to switchboard. The smoke test's files are the working example throughout: [switchboard.yaml](../../tests/smoke/switchboard.yaml), [agw.yaml](../../tests/smoke/agw.yaml) and [compose.yaml](../../tests/smoke/compose.yaml).

This page sets up reads. To let agents write with your approval, continue with [Approve writes on your phone](approvals.md).

## Before you start

You need:

- An agentgateway route that serves the MCP servers you want behind switchboard, with `prefixMode: always`, so the gateway names each tool `<target>_<tool>`. The smoke test's `homelab` route is one.
- A key for switchboard on that route.
- A value for switchboard's own bearer, which only the gateway will hold.

switchboard splits a tool name at its first `_` and assumes target names contain none (`split_name` in [catalog.py](../../src/switchboard/catalog.py)). A target whose name contains `_` never matches its own `servers` entry.

## Write the config

```yaml title="config.yaml"
upstream: http://agentgateway:3000        # the gateway, as switchboard reaches it
ingress_token_env: SWITCHBOARD_TOKEN      # the bearer the gateway must send
scopes:
  everything: {upstream_route: /mcp/everything, key_env: GATEWAY_KEY, writes: "off"}
servers:                                  # keyed by the gateway's target name
  github: {about: "GitHub, read-only token", trust_annotations: true}
  notes:  {about: "Notes", read: ["^(get|list|search)_"]}
```

- `upstream` plus a scope's `upstream_route` is the URL of that scope's session, here `http://agentgateway:3000/mcp/everything`. switchboard sends the value of the scope's `key_env` variable to it as `Authorization: Bearer`.
- Each scope becomes an MCP endpoint at `/mcp/<scope>`. Two scopes can read the same route with different `writes`; the [README](../../README.md#config) pairs `everything` with a `readonly` scope.
- `about` is the server's line in the `search` catalog, and search matches its words too. Say what the server is for.
- The file holds variable names, never secret values.

switchboard checks every key at start. An unknown key, a missing required key, or a variable that is unset or empty stops it with an error that names the key or variable ([config.py](../../src/switchboard/config.py), [test_config.py](../../tests/test_config.py)). The [reference](reference.md#configuration) lists every key and default, including the `results` block for page sizes and the paging cache.

## Mark the tools that read

A tool runs through `read`, with no approval, only if its server's entry makes it a read tool. Every other tool goes through `write`.

- `read:` is a list of Python regular expressions matched with `re.search` against the tool name without its `<target>_` prefix. Anchor them: `^get_` matches `get_note`, while `get` alone also matches `forget_note`.
- `write:` is the same kind of list, and a match wins over `read:`. Use it to carve exceptions out of a broad `read:` pattern.
- `trust_annotations: true` makes a tool read when the server marks it `readOnlyHint`. Set it only for servers whose annotations you trust.

A server with no entry is all `write`, and a tool marked `destructiveHint` is always `write`. Both lists must be YAML lists: switchboard refuses a bare string, because it would compile one character at a time (`_patterns` in [config.py](../../src/switchboard/config.py)). [How it works](how-it-works.md#read-or-write) gives the full order of the rules.

To check the result, call `search` with `{"server": "<target>", "limit": 20}`. Each line shows `[read]` or `[write]`, and `describe` returns the same as `verb`. One call lists at most 20 tools; for a larger server, search by words.

## Set the environment

| variable | default | what it holds |
|---|---|---|
| `SWITCHBOARD_CONFIG` | `/config/config.yaml` | the config file's path |
| `SWITCHBOARD_PORT` | `8000` | the port for MCP and `/healthz` |
| `SWITCHBOARD_METRICS_PORT` | `9109` | the Prometheus port |
| each name in a `*_env` key | none | the secret that key names |

[`__main__.py`](../../src/switchboard/__main__.py) reads the first three.

## Run the container

The image runs as user 65534 and starts `switchboard` ([Dockerfile](../../Dockerfile)). Mount the config where `SWITCHBOARD_CONFIG` points, readable by that user, and pass every variable the config names:

```bash title="Run the published image"
docker run -d --name switchboard --network <gateway-network> \
  -v "$PWD/config.yaml:/config/config.yaml:ro" \
  -e SWITCHBOARD_TOKEN -e GATEWAY_KEY \
  ghcr.io/egoushka/switchboard:0.1.0
```

`<gateway-network>` is a Docker network on which the host in `upstream` resolves and the gateway can reach switchboard on port 8000. [compose.yaml](../../tests/smoke/compose.yaml) does the same with Compose.

At start, switchboard asks each scope's route for its tools, waiting at most 20 seconds per scope, to put each server's tool count in the `search` description. If the gateway does not answer, the description lists the configured servers without counts until the next restart (`lifespan` in [server.py](../../src/switchboard/server.py)).

## Route clients through the gateway

Clients reach switchboard through a gateway route that has switchboard as its MCP target. This is the smoke test's `lab-ro` route from [agw.yaml](../../tests/smoke/agw.yaml), with a placeholder key and client name:

```yaml title="agentgateway route in front of switchboard"
- name: lab-ro
  matches: [{path: {exact: /mcp/lab-ro}}]
  policies:
    apiKey:
      mode: strict
      keys: [{keyHash: "sha256:<hash of the client key>", metadata: {client: laptop}}]
    transformations:
      request:
        set:
          x-switchboard-client: apiKey.client
  backends:
  - mcp:
      prefixMode: always
      failureMode: failOpen
      targets:
      - name: lab
        mcp: {host: "http://switchboard:8000/mcp/homelab-ro"}
        policies: {backendAuth: {key: "${SWITCHBOARD_TOKEN}"}}
```

- `backendAuth` sends switchboard's bearer. Without it, switchboard answers every request with `401 unauthorized`.
- The `x-switchboard-client` header names the client in approval messages and audit lines. Without it, switchboard uses the scope name.
- With `prefixMode: always` and the target `lab`, clients see `lab_search`, `lab_describe`, `lab_read` and `lab_more`, and `lab_write` where the scope allows writes.

## Check it

```bash
curl -s http://<switchboard-host>:8000/healthz
```

It prints `ok`; `/healthz` needs no bearer. Any other path without the bearer answers `401`. The image defines no Docker `HEALTHCHECK`, so point your orchestrator at `/healthz`.

Prometheus metrics are at `http://<switchboard-host>:9109/metrics`. The [reference](reference.md#metrics) lists the series.

> [!WARNING]
> Port 9109 has no authentication, and both ports listen on all interfaces ([`__main__.py`](../../src/switchboard/__main__.py)). switchboard also turns off the MCP SDK's DNS-rebinding protection (`build_app` in [server.py](../../src/switchboard/server.py)), so on port 8000 the bearer is the only guard. Keep both ports on a network that only the gateway and your Prometheus can reach.

## Measure what it saves

[measure.py](../../scripts/measure.py) connects to one MCP route and prints, per server, the tool count and the characters of their definitions (name, description and input schema), then a total with a rough token count, the characters divided by 4. Run it once against the route switchboard reads and once against the route your clients use for switchboard:

```bash title="Compare the two tool lists"
MCP_KEY=<key for the route> uv run scripts/measure.py http://<gateway>/mcp/everything
MCP_KEY=<client key> uv run scripts/measure.py http://<gateway>/mcp/<client route>
```

switchboard's own list is fixed except the catalog in the `search` description, which grows by one line per server, so what you save depends on how many tools the route has. The repository records no measurement.
