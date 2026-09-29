---
title: "Reference"
description: "The five tools and their arguments, tool errors, every config key with its default, environment variables, routes, metrics, audit fields and fixed limits."
order: 5
section: "Reference"
---

## Tools

Every scope offers `search`, `describe`, `read` and `more`. A scope with `writes: approve` also offers `write`. The input schemas come from the method signatures of `ScopeTools` in [server.py](../../src/switchboard/server.py).

| tool | `readOnlyHint` | `destructiveHint` | `openWorldHint` |
|---|---|---|---|
| `search` | `true` | unset | `true` |
| `describe` | `true` | unset | `true` |
| `read` | `true` | unset | `true` |
| `write` | `false` | `true` | `true` |
| `more` | `true` | unset | `true` |

| tool | argument | default | meaning |
|---|---|---|---|
| `search` | `query` | `""` | words to rank tools by |
| `search` | `server` | `""` | only this server; with no query, list its tools |
| `search` | `limit` | `8` | how many results, clamped to 1–20 |
| `describe` | `tools` | required | tool names; only the first 5 are used |
| `read`, `write` | `tool` | required | the full name, `<target>_<tool>` |
| `read`, `write` | `args` | `null` | the tool's arguments; `null` sends `{}` |
| `read`, `write` | `query` | `""` | JMESPath applied to a JSON result |
| `read`, `write` | `max_chars` | `null` | first page size; `null` uses `results.max_chars` |
| `write` | `reason` | required | shown to the approver and logged |
| `more` | `result_id` | required | from the trailer of a cut result |
| `more` | `offset` | required | from the trailer; below 0 reads as 0 |

A `max_chars` you pass is clamped to between 1 and `results.hard_max_chars`.

**`search`** returns one line per tool, `<tool> [read|write] — <summary>`, or `No matching tool. Servers: <names>`. Its description ends with the catalog: `Servers:`, then one line per server, `- <server> (<n> tools): <about>`, or `- <server>: <about>` when the gateway did not answer at start.

**`describe`** returns a JSON array with `{"tool", "verb", "description", "input_schema"}` for each known tool and `{"tool", "error"}` for each unknown one. The schema is compacted as [How it works](how-it-works.md#the-catalog-and-search) describes.

**`read`** runs a read tool and returns the shaped result. A result longer than the page ends with a trailer that names the `result_id` and the next `offset`. The tool's description gives the default page as 6000 characters even when `results.max_chars` is set to another value.

**`write`** accepts any tool in the scope, read tools included. It waits for approval, then returns the shaped result, or an error; [Approve writes on your phone](approvals.md#what-the-agent-gets-back) lists each outcome.

**`more`** returns the page at `offset`, the same size as the first page, with a trailer until the last page. At or past the end it returns `[end of result: <n> chars]`.

## Tool errors

switchboard returns these as tool results with `isError: true`, which the model can read and act on (`guarded` in [server.py](../../src/switchboard/server.py)).

| message starts with | cause |
|---|---|
| `unknown tool '<name>'` | not in the scope's catalog; may add `did you mean:` |
| `<tool> is a write tool` | `read` was called with a write tool |
| `query needs a JSON result` | a `query` on a plain-text result |
| `bad query:` | the JMESPath does not compile or fails |
| `result is over 5000000 chars` | the shaped result passed the ceiling |
| `result expired` | the id is unknown, expired, evicted, or another scope's |
| `upstream <scope> unavailable` | switchboard could not reach the gateway route |
| `<scope>: <message>` | the upstream answered with a JSON-RPC error |

A tool that returns its own error result passes it through with the error flag set. The errors that only `write` returns are in [Approve writes on your phone](approvals.md#what-the-agent-gets-back).

## Configuration

A YAML file, read once at start from the path in `SWITCHBOARD_CONFIG` (`load` in [config.py](../../src/switchboard/config.py)). An unknown key at any level, a missing required key, or a variable that is unset or empty stops the process with a `ConfigError` that names it.

| key | default | meaning |
|---|---|---|
| `upstream` | required | the gateway's base URL; a trailing `/` is dropped |
| `ingress_token_env` | required | variable holding the bearer the gateway sends |
| `scopes` | required | one entry per scope, at least one |
| `scopes.<name>.upstream_route` | required | path after `upstream` for this scope's session |
| `scopes.<name>.key_env` | required | variable holding the gateway key for that route |
| `scopes.<name>.writes` | required | `approve` or `off`; a bare YAML `off` works too |
| `scopes.<name>.approval_timeout_s` | `approval.timeout_s` | seconds this scope waits for a tap |
| `servers` | none | one entry per gateway target |
| `servers.<target>.about` | required | the catalog line; search matches it too |
| `servers.<target>.read` | `[]` | regexes for tools that read |
| `servers.<target>.write` | `[]` | regexes for tools that always write |
| `servers.<target>.trust_annotations` | `false` | treat `readOnlyHint` as read |
| `approval` | none | required when a scope has `writes: approve` |
| `approval.telegram_token_env` | required | variable holding the bot token |
| `approval.approver_id_env` | required | variable holding the approver's numeric user id |
| `approval.timeout_s` | `50` | seconds a request waits for a tap |
| `results.max_chars` | `6000` | first page size when a call gives none |
| `results.hard_max_chars` | `40000` | the most a call's `max_chars` can ask for |
| `results.cache_ttl_s` | `600` | seconds a cut result can be paged |
| `results.cache_max_bytes` | `32000000` | memory for cut results; the oldest go first |

- A scope's name is its path `/mcp/<name>`, its MCP server name `switchboard-<name>`, the `scope` label on metrics and the `scope` field of audit lines.
- `read` and `write` must be lists of strings that compile as Python regular expressions, and `trust_annotations` must be a YAML boolean. [How it works](how-it-works.md#read-or-write) gives the order in which they apply.
- The approver id must be digits only.
- `cache_max_bytes` counts the strings' size in memory, so text outside Latin-1 takes two or four bytes per character (`test_cache_counts_real_memory_not_chars` in [test_shaping.py](../../tests/test_shaping.py)).

## Environment variables

| variable | default | read by |
|---|---|---|
| `SWITCHBOARD_CONFIG` | `/config/config.yaml` | the entrypoint: the config file's path |
| `SWITCHBOARD_PORT` | `8000` | the entrypoint: MCP and `/healthz` |
| `SWITCHBOARD_METRICS_PORT` | `9109` | the entrypoint: Prometheus |
| each name in a `*_env` key | none | the config: the secret it names |
| `MCP_KEY` | none | [measure.py](../../scripts/measure.py): the route's bearer |

The first three are read in [`__main__.py`](../../src/switchboard/__main__.py).

## Routes and ports

| where | auth | answers |
|---|---|---|
| `GET /healthz`, port 8000 | none | `ok` |
| `/mcp/<scope>`, port 8000 | the ingress bearer | MCP over Streamable HTTP |
| any other path, port 8000 | the ingress bearer | `401 unauthorized` without it |
| any `GET`, port 9109 | none | Prometheus text format |

Both ports listen on all interfaces. The ingress bearer is `Authorization: Bearer <value of the ingress_token_env variable>`.

The request header `x-switchboard-client` names the client in approval messages and audit lines. Without it, switchboard uses the scope name.

## Metrics

Defined in [metrics.py](../../src/switchboard/metrics.py).

| series | type | labels |
|---|---|---|
| `switchboard_calls_total` | counter | `scope`, `server`, `verb`, `outcome` |
| `switchboard_result_chars_total` | counter | `scope`, `server`, `stage` |
| `switchboard_approvals_total` | counter | `scope`, `decision` |
| `switchboard_approval_wait_seconds` | histogram | none |
| `switchboard_catalog_tools` | gauge | `scope` |
| `switchboard_upstream_errors_total` | counter | `scope` |

- `switchboard_calls_total` counts upstream calls that returned a result; `outcome` is `ok` or `error`, from the result's error flag. A call that raised, including an approved write whose result is unknown, is not counted there.
- `switchboard_result_chars_total` has `stage="upstream"`, the text a client would have received from the gateway route, and `stage="returned"`, the first page switchboard sent.
- `switchboard_approvals_total` has `decision` `approved`, `denied` or `expired`. A request Telegram could not take is not counted.
- `switchboard_approval_wait_seconds` has buckets at 5, 10, 20, 30, 45, 60, 90, 120 and 180 seconds.
- `switchboard_upstream_errors_total` counts failed connects and lost sessions, not JSON-RPC errors the upstream answers with.

## Audit log

JSON lines on the `switchboard.audit` logger, which the entrypoint writes to stderr: two for an approved write, one for any other decision (`_audit` in [server.py](../../src/switchboard/server.py)).

| field | value |
|---|---|
| `event` | always `write` |
| `scope` | the scope's name |
| `client` | the `x-switchboard-client` header, or the scope name |
| `tool` | the full tool name |
| `args_sha256` | SHA-256 of the arguments; never the arguments |
| `reason` | the first 200 characters of `reason` |
| `approval_id` | the request's id; absent when Telegram could not take it |
| `ts` | UTC time of the line, to the second |
| `decision` | `approved`, `denied`, `expired` or `unavailable` |
| `wait_ms` | milliseconds since `write` began asking |
| `outcome` | `running`, then `ok`, `error` or `unknown`; `-` if nothing ran |

`args_sha256` hashes `json.dumps(args, sort_keys=True, ensure_ascii=False)`. [Read the audit log](approvals.md#read-the-audit-log) has an example and a command that reproduces the hash.

## Limits

None of these can be changed in the config.

| limit | value | where |
|---|---|---|
| tools per `describe` call | 5 | server.py |
| `search` results | 1 to 20, default 8 | server.py |
| summary in a `search` line | first sentence, 140 characters | catalog.py |
| tool catalog refresh | 300 s | upstream.py, `ToolCache` |
| wait for the catalog at start | 20 s per scope | server.py, `lifespan` |
| shaped result ceiling | 5,000,000 characters | shaping.py, `MAX_RESULT_CHARS` |
| wait for an upstream answer | 300 s | upstream.py, `call_tool` |
| an approved write, shielded | 300 s | server.py, `WRITE_TIMEOUT_S` |
| string value in an approval message | 200 characters | approval.py, `VALUE_CAP` |
| key in an approval message | 100 characters | approval.py, `_capped` |
| arguments in an approval message | 2,500 characters | approval.py, `ARGS_CAP` |
| reason in an approval message | 500 characters | approval.py, `render` |
| reason in an audit line | 200 characters | server.py, `write` |
| progress while waiting for a tap | every 15 s | approval.py, `Approver` |
| Telegram long poll | 50 s, then 5 s pause after a failure | approval.py, `poll_forever` |
| a Bot API send, edit or answer | 15 s | approval.py, `BotAPI` |
