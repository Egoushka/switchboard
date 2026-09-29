---
title: "How it works"
description: "The request path through gateway and switchboard, how search ranks tools, how a tool becomes read or write, how results shrink, and how a write runs."
order: 4
section: "Concepts"
---

## The parts

A request crosses the gateway twice: once on the route a client uses, once on the route that holds the MCP servers.

```text
MCP client
  |  the client's key
  v
agentgateway, client route      checks the key, sets x-switchboard-client
  |  Authorization: Bearer <ingress token>
  v
switchboard, /mcp/<scope>       search, describe, read, write, more
  |  Authorization: Bearer <scope key>
  v
agentgateway, upstream route    merges the MCP servers, names tools <target>_<tool>
  |
  v
MCP servers
```

For writes, switchboard also talks to the Telegram Bot API. Prometheus scrapes it on its own port.

| module | what it holds |
|---|---|
| [server.py](../../src/switchboard/server.py) | the five tools, one app per scope, the bearer, `/healthz` |
| [catalog.py](../../src/switchboard/catalog.py) | read or write, BM25 search, schemas, catalog lines |
| [upstream.py](../../src/switchboard/upstream.py) | one long-lived session per scope, and the tool cache |
| [shaping.py](../../src/switchboard/shaping.py) | trimming, JMESPath, the paging cache |
| [approval.py](../../src/switchboard/approval.py) | Telegram approvals |
| [config.py](../../src/switchboard/config.py) | loading and checking the YAML, secrets from variables |
| [metrics.py](../../src/switchboard/metrics.py) | the Prometheus series |
| [`__main__.py`](../../src/switchboard/__main__.py) | the entrypoint: logging, ports, wiring |

## Scopes and the bearer

Each scope in the config becomes its own MCP server, `switchboard-<scope>`, at `/mcp/<scope>`, with its own upstream session and tool cache. All scopes share one web app on port 8000, one paging cache and one Telegram approver (`build_app` in [server.py](../../src/switchboard/server.py)).

Every request except `GET /healthz` must carry `Authorization: Bearer <ingress token>`. switchboard compares it in constant time and answers anything else with `401 unauthorized` (`BearerAuth`). The token is the value of the variable that `ingress_token_env` names. Only the gateway holds it, so a client cannot go around the gateway's key checks and allow-lists.

## The catalog and search

switchboard reads the upstream route's `tools/list`, every page of it, and keeps the result per scope (`ToolCache` in [upstream.py](../../src/switchboard/upstream.py)). The first call that needs the list after 300 seconds fetches it again. If that fetch fails, switchboard serves the last good list for another 300 seconds. For each tool it keeps the server (the name before the first `_`), read or write, the destructive flag, the description, a summary (the first sentence, at most 140 characters) and the input schema.

`search` scores tools with BM25 (k1 = 1.5, b = 0.75) over four fields read as one text: the server name, the server's `about`, the tool name and the description (`Index` in [catalog.py](../../src/switchboard/catalog.py)). Words are split at camelCase and snake_case boundaries and lowercased, and common English words such as "the" and "of" are dropped, so `getChatHistory` matches "chat history". Equal scores sort by tool name. `limit` is 8 by default and at most 20. Each hit is one line:

```text
oura_sleep [read] — One night's sleep score.
```

Two cases skip the ranking. An empty query with a `server` returns the first `limit` of that server's tools, in the gateway's order. An empty query with no server returns no tools, only the server names.

The `search` description carries the catalog: one line per server with its tool count and its `about`, built once at start. If the gateway does not answer at start, the lines list every configured server without counts, and they stay that way until a restart.

A name that `describe`, `read` or `write` does not know gets an error that suggests the three best search matches for it, such as `unknown tool 'oura_slep'; did you mean: oura_sleep`. `describe` returns a tool's input schema without what a model does not need: `$schema`, `additionalProperties: false`, `default: null`, an empty `required`, and a property `title` that only repeats the property's name (`compact_schema`).

## Read or write

`classify` in [catalog.py](../../src/switchboard/catalog.py) decides once per tool, when the catalog is built:

| check | if it holds, the tool is |
|---|---|
| its server has no `servers` entry | write |
| it sets `destructiveHint` | write |
| a `write:` regex of its server matches its name | write |
| a `read:` regex of its server matches its name | read |
| `trust_annotations: true` and the tool sets `readOnlyHint` | read |
| none of the above | write |

The first three checks come before the two that make a tool read. The regexes run with `re.search` against the name without the `<target>_` prefix. A server's own annotations count only where you set `trust_annotations`, and even then a destructive hint wins.

`read` refuses a write tool with `<tool> is a write tool; use write(tool, args, reason)`. A read-only scope has no `write` tool at all, so a write tool cannot run there ([test_catalog.py](../../tests/test_catalog.py), [test_integration.py](../../tests/test_integration.py)).

## Results and paging

`read` and `write` put the upstream result through the same steps (`shape` in [server.py](../../src/switchboard/server.py), [shaping.py](../../src/switchboard/shaping.py)):

1. **Pick.** `structuredContent` wins when present, and its text copy is dropped. Otherwise a single text block that parses as JSON is JSON. Anything else is plain text, its text blocks joined by newlines.
2. **Drop empties.** In JSON, object keys whose value is `null`, `""`, `[]` or `{}` go, at any depth. Every list element stays, so positions in table rows hold. `0` and `false` stay.
3. **Query.** An optional JMESPath `query` projects the JSON. A plain-text result refuses a query.
4. **Serialize.** The JSON is written compact. Past 5,000,000 characters, JSON is refused with a request to narrow it, and plain text is cut with a note.
5. **Page.** The first page is `max_chars` long: 6,000 by default (`results.max_chars`), and a call may ask for 1 to 40,000 (`results.hard_max_chars`). A longer result goes into the paging cache, and the page ends with a trailer.

This is the trailer of a 20,000-character result read with `max_chars` 1000, as in `test_a_big_result_pages_through_more`:

```text
…[truncated: 1000 of 20000 chars — more(result_id="r_<id>", offset=1000), or narrow with query]
```

`more` returns the next page, the same size as the first. A result id works only in the scope that made it, for 600 seconds by default (`results.cache_ttl_s`). When the cache passes `results.cache_max_bytes`, 32,000,000 by default, the oldest results go first ([test_shaping.py](../../tests/test_shaping.py)). Non-text blocks, such as images, pass through after the text. An upstream tool error keeps its error flag.

`switchboard_result_chars_total` counts both sides of this: `stage="upstream"` is the text a client would have received from the gateway route directly, `stage="returned"` the first page switchboard sent.

## The upstream session

Each scope holds one MCP client session to its gateway route. It opens on first use and lives in a task of its own, because the SDK's context managers must enter and exit in one task (`Upstream` in [upstream.py](../../src/switchboard/upstream.py)).

When a call fails with a transport error, with "Connection closed" (-32000), or with "Session not found" (-32600, the answer after an agentgateway restart), switchboard drops that session:

- `tools/list` and `read` open a new session and try once more.
- `write` does not retry. The next call opens a new session.
- A late failure on an old session never drops a newer one that another call has opened.

Any other JSON-RPC error is the server's answer: the session stays, and the model gets the message. A tool call waits at most 300 seconds for the upstream's answer. [test_upstream.py](../../tests/test_upstream.py) covers each case, including a stand-in gateway that forgets its sessions mid-test.

## An approved write, step by step

1. The scope has `writes: approve` and the config has an `approval` block; otherwise `write` is not registered.
2. switchboard looks up the tool and compiles the `query`, if there is one, so a bad query fails before anyone is asked.
3. It hashes the arguments and starts the audit record.
4. `Approver.ask` copies the arguments, renders the message (refusing arguments too large to show), stores a pending request under a random id, and sends it with Approve and Deny buttons. If Telegram refuses or cannot be reached, nothing stays pending and the write fails closed.
5. The poll loop long-polls `getUpdates` for button taps. A tap settles a request only if it comes from the approver's user id and names a pending request. The first of approve, deny and the deadline wins, and the request leaves the pending set, so a replayed tap finds nothing.
6. Denied or expired: one audit line, an error for the model, and nothing runs.
7. Approved: the audit line with `outcome` `running` is written first. The upstream call then runs with the copied arguments, once, shielded from the client's cancellation, for at most 300 seconds. A second audit line records `ok`, `error` or `unknown`, and the bot edits the message with the outcome.
8. The result goes through the same shaping as a `read`.

The code is `write` in [server.py](../../src/switchboard/server.py) and `Approver` in [approval.py](../../src/switchboard/approval.py). [Approve writes on your phone](approvals.md) shows what the approver and the agent see at each outcome.
