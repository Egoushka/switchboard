# switchboard

One MCP front door to every tool behind an MCP gateway. An agent sees five
tools instead of hundreds:

- `search` finds a tool;
- `describe` returns its arguments;
- `read` runs it;
- `write` runs it after a person approves it on their phone;
- `more` pages a long result.

Every result is trimmed: nulls are dropped, an optional JMESPath `query` is
applied, and the first page is capped at 6k chars.

It is built to sit behind [agentgateway](https://agentgateway.dev), which owns
client keys, allow-lists and upstream credentials. switchboard:
- serves one MCP app per *scope* at `/mcp/<scope>`;
- holds one long-lived session to that scope's gateway route, and replaces the
  session if the gateway forgets it;
- accepts only the gateway's own bearer.

## Read or write

Only config makes a tool `read`, which runs without approval:
- per-server `read:` regexes;
- or `trust_annotations: true`, which trusts a server's `readOnlyHint`.

`write:` regexes and a `destructiveHint` always force `write`. A server with
no config entry is all `write`.

## Writes

Each write waits (50 s by default) for Approve/Deny from a dedicated Telegram
bot. The approval message shows:
- every argument, with long values capped one by one;
- zero-width and bidi characters as visible escapes.

A write that cannot be shown in full is refused. Approvals are single-use,
bound to exactly the arguments shown, denied on timeout, and fail closed when
Telegram is unreachable.

Once approved, the call runs to the end even if the client gives up. The
decision goes to an audit line before the call, and the outcome after it
(`args_sha256`, never the arguments).

## Config

```yaml
upstream: http://agentgateway:3000
ingress_token_env: SWITCHBOARD_TOKEN          # the bearer the gateway sends (backendAuth)
approval:                                     # needed only if a scope has writes: approve
  telegram_token_env: SWITCHBOARD_TELEGRAM_TOKEN
  approver_id_env: SWITCHBOARD_APPROVER_ID
  timeout_s: 50
scopes:
  everything: {upstream_route: /mcp/everything, key_env: GATEWAY_KEY, writes: approve}
  readonly:   {upstream_route: /mcp/everything, key_env: GATEWAY_KEY, writes: "off"}
servers:                                      # keyed by the gateway's target name (tool prefix)
  github:   {about: "GitHub, read-only token", trust_annotations: true}
  telegram: {about: "Telegram account", trust_annotations: true, write: ["invite"]}
```

Secret values come from the env vars the file names. MCP listens on `:8000`,
Prometheus on `:9109`. Set `SWITCHBOARD_CONFIG` to the file's path.

## Run

- Tests: `uv sync && uv run pytest`
- Smoke test through a real agentgateway:
  `docker build -t switchboard:dev . && docker compose -f tests/smoke/compose.yaml up -d && uv run python tests/smoke/check.py`
- Measure a route's tool definitions: `MCP_KEY=... uv run scripts/measure.py <url>`
