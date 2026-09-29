---
title: "Approve writes on your phone"
description: "Give switchboard a Telegram bot and an approver, allow writes in a scope, and read what the approver sees, what the agent gets back and what is logged."
order: 3
section: "Guides"
---

A scope with `writes: approve` offers the `write` tool. Each call sends a message with Approve and Deny buttons to one Telegram user, and the tool runs only after that user taps Approve. This page sets it up and shows what each side sees.

## What you need

- **A Telegram bot for switchboard alone.** switchboard reads button taps by long-polling the Bot API's `getUpdates` (`BotAPI` in [approval.py](../../src/switchboard/approval.py)), and the [README](../../README.md#writes) asks for a dedicated bot. Create one with Telegram's BotFather and keep its token.
- **The approver's numeric Telegram user id.** The config refuses anything but digits, so an `@username` does not work ([test_config.py](../../tests/test_config.py), `test_approver_id_must_be_digits`).
- **A chat between the approver and the bot.** Open the bot in Telegram and press Start: a bot can message only users who have started a chat with it.

## Allow writes

Add an `approval` block and set `writes: approve` on a scope:

```yaml title="config.yaml"
approval:
  telegram_token_env: SWITCHBOARD_TELEGRAM_TOKEN
  approver_id_env: SWITCHBOARD_APPROVER_ID
  timeout_s: 50
scopes:
  everything: {upstream_route: /mcp/everything, key_env: GATEWAY_KEY, writes: approve}
  readonly:   {upstream_route: /mcp/everything, key_env: GATEWAY_KEY, writes: "off"}
```

- `approval` is required as soon as one scope has `writes: approve`; without it switchboard does not start ([config.py](../../src/switchboard/config.py)).
- `timeout_s` is how long a request waits for a tap: 50 seconds when you leave it out. A scope's `approval_timeout_s` overrides it for that scope.
- Pass both variables to the container and restart it. switchboard reads its config only at start.

The `everything` scope now lists five tools and `readonly` still lists four (`test_writable_scope_adds_write` in [test_integration.py](../../tests/test_integration.py)). `write` accepts any tool in the scope, read tools included, and always asks.

## What the approver sees

The bot sends a message like this, with Approve and Deny buttons under it:

```text
Write request · everything · laptop
notes_add
the user asked for a reminder
{
 "text": "buy milk"
}
```

The first line names the scope and the client (the `x-switchboard-client` header, or the scope name). Then come the tool, the agent's `reason` (up to 500 characters) and the arguments as JSON. A tool that sets `destructiveHint` gets a red destructive marker after its name. The message is built so that it cannot hide what will run (`render` in [approval.py](../../src/switchboard/approval.py)):

- Each string value is cut at 200 characters and ends with the count it dropped, such as `…(+1400)`. A long first value cannot push a later argument out of sight.
- Zero-width and bidi control characters appear as escapes such as `\u202e`, in the arguments and in every other line.
- If the arguments still take more than 2,500 characters, switchboard refuses the write and sends nothing.

Only the approver's taps count. A tap from another account, a second tap, and a tap after the deadline each get the answer "expired or unknown" and change nothing ([test_approval.py](../../tests/test_approval.py)). The bot then edits the message to add the outcome: denied or expired at once, or, for an approved write, the upstream's result once the call ends.

## What the agent gets back

| what happened | `write` returns | the tool ran |
|---|---|---|
| approved | the tool's result, shaped like `read` | yes |
| denied | error `denied; nothing was executed` | no |
| no tap before the timeout | error `expired; nothing was executed` | no |
| Telegram unreachable | error `approval unavailable (…)` | no |
| arguments too large to show | error `approval unavailable (…)` | no |
| `query` does not compile | error `bad query: …`, before anyone is asked | no |
| approved, upstream call failed | error `approved, but the result is unknown: …` | unknown |

The texts come from `write` in [server.py](../../src/switchboard/server.py). The write tests in [test_integration.py](../../tests/test_integration.py) cover every row but two: an unreachable Telegram, which [test_approval.py](../../tests/test_approval.py) covers, and an upstream failure after approval, which no test covers.

- The `expired` error adds a hint to get approval and call `write` again.
- An approved write runs the arguments the message was built from, once. It is never retried on a new upstream session (`test_a_lost_session_on_a_write_is_dropped_but_not_retried` in [test_upstream.py](../../tests/test_upstream.py)).
- Once approved, the call runs to the end even if the client disconnects, for at most 300 seconds (`WRITE_TIMEOUT_S`). If the upstream fails or does not answer in that time, the result is unknown: the write may or may not have happened.
- If the call is cancelled while switchboard waits for the tap, the request settles as expired and nothing runs (`test_cancelled_wait_settles_expired` in [test_approval.py](../../tests/test_approval.py)).
- A `query` that compiles but does not fit the result does not turn a finished write into an error. The result comes back without the query, after the note `[the write ran; query not applied: …]`.
- While it waits, `write` reports progress every 15 seconds, as seconds waited out of the timeout, to clients that asked for progress. Give your client a tool timeout longer than the approval timeout.

## Read the audit log

switchboard logs write decisions as JSON lines on the `switchboard.audit` logger, which the entrypoint writes to stderr (`_audit` in [server.py](../../src/switchboard/server.py), [`__main__.py`](../../src/switchboard/__main__.py)). An approved write logs two lines. The first comes before the upstream call, with `outcome` `running`, so the approval is on record even if the process dies during the call. The second records `ok`, `error` or `unknown`. A denied, expired or unavailable request logs one line with `outcome` `-`.

```json title="The first line of an approved write (example values)"
{"event": "write", "scope": "everything", "client": "laptop", "tool": "notes_add", "args_sha256": "<sha256 of the arguments>", "reason": "the user asked for a reminder", "approval_id": "<request id>", "ts": "2026-09-29T09:12:03Z", "decision": "approved", "wait_ms": 4180, "outcome": "running"}
```

The arguments never appear. `args_sha256` is the SHA-256 of `json.dumps(args, sort_keys=True, ensure_ascii=False)`, so you can check whether a given set of arguments is the one in a line:

```bash title="Reproduce args_sha256"
python3 -c 'import hashlib, json, sys; print(hashlib.sha256(json.dumps(json.loads(sys.argv[1]), sort_keys=True, ensure_ascii=False).encode()).hexdigest())' '{"text": "buy milk"}'
```

The [reference](reference.md#audit-log) lists every field. Two metrics follow the same decisions: `switchboard_approvals_total` counts approved, denied and expired requests per scope, and `switchboard_approval_wait_seconds` times the wait.

## Limits

- One approver and one bot per process (`Approval` in [config.py](../../src/switchboard/config.py)).
- Each approval covers one call. There is no standing approval for a tool, a client or a session.
- Pending requests live in memory. After a restart, a tap on an older message gets "expired or unknown".
