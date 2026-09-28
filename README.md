# switchboard

One MCP front door to every homelab tool. An agent sees five tools instead of
hundreds:

- `search` finds a tool;
- `describe` returns its arguments;
- `read` runs it;
- `write` runs it after Yehor approves on his phone;
- `more` pages a long result.

Every result is trimmed: nulls dropped, an optional JMESPath `query`, a 6k-char
first page.

It sits behind [agentgateway](https://agentgateway.dev), which owns client
keys, allow-lists and upstream credentials, and serves one MCP app per scope at
`/mcp/<scope>`. Design: `docs/superpowers/specs/2026-09-28-switchboard-design.md`.

- Run the tests: `uv sync && uv run pytest`
- Run the smoke test through a real agentgateway: `tests/smoke/check.py` (see
  the plan, Task 8 Step 5)
- Measure a route's tool definitions: `MCP_KEY=... uv run scripts/measure.py <url>`
- Config: `SWITCHBOARD_CONFIG` (YAML; secret values come from the env vars it
  names). MCP listens on `:8000`, Prometheus on `:9109`.
