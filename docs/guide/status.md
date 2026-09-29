---
title: "Status and evidence"
description: "What works, what is partial and what does not exist in switchboard v0.1.0, with the test, file or CI step behind each."
order: 6
section: "Project"
---

Each row names its evidence. `works` means a test in this repository covers it. `partial` means part of it has no test, or only the hand-run smoke test covers it. `not yet` means it does not exist.

The 75 tests call no network service. They run switchboard in-process or on `127.0.0.1`, with an in-process MCP server standing in for the gateway and a fake standing in for Telegram (`fake_upstream` and `FakeTelegram` in [test_integration.py](../../tests/test_integration.py)). The only run against a real agentgateway is the smoke test in [tests/smoke](../../tests/smoke/), which you start by hand.

## Status

| Capability | Status | Evidence |
|---|---|---|
| Bearer on every path but `/healthz` | works | [test_integration.py](../../tests/test_integration.py) |
| Four tools, or five where writes are allowed | works | [test_integration.py](../../tests/test_integration.py) |
| Search, describe and the catalog lines | works | [test_catalog.py](../../tests/test_catalog.py), [test_integration.py](../../tests/test_integration.py) |
| Read or write decided by config | works | [test_catalog.py](../../tests/test_catalog.py) |
| Config checks | works | [test_config.py](../../tests/test_config.py) |
| Result shaping and paging | works | [test_shaping.py](../../tests/test_shaping.py), [test_integration.py](../../tests/test_integration.py) |
| Telegram approvals: single-use, approver only, deadline | works | [test_approval.py](../../tests/test_approval.py) |
| Approval messages that cannot hide arguments | works | [test_approval.py](../../tests/test_approval.py) |
| Approved writes: once, shielded, audited | works | [test_integration.py](../../tests/test_integration.py) |
| Upstream session recovery | works | [test_upstream.py](../../tests/test_upstream.py) |
| Tool catalog refresh | works | [test_upstream.py](../../tests/test_upstream.py) |
| Bot token kept out of errors and logs | works | [test_approval.py](../../tests/test_approval.py) |
| A write whose upstream call fails after approval | partial | [server.py](../../src/switchboard/server.py) |
| Through a real agentgateway | partial | [tests/smoke](../../tests/smoke/) |
| Container image | partial | [Dockerfile](../../Dockerfile), [ci.yml](../../.github/workflows/ci.yml) |
| Entrypoint and environment variables | partial | [`__main__.py`](../../src/switchboard/__main__.py) |
| Prometheus metrics | partial | [metrics.py](../../src/switchboard/metrics.py) |
| Progress while waiting for a tap | partial | [test_approval.py](../../tests/test_approval.py) |
| Catalog lines when the gateway is down at start | partial | [test_upstream.py](../../tests/test_upstream.py), [test_catalog.py](../../tests/test_catalog.py) |
| A recorded measurement of the tool-list saving | not yet | [measure.py](../../scripts/measure.py) |
| stdio transport | not yet | [server.py](../../src/switchboard/server.py) |
| Config reload without a restart | not yet | [`__main__.py`](../../src/switchboard/__main__.py) |
| State that survives a restart | not yet | [approval.py](../../src/switchboard/approval.py), [shaping.py](../../src/switchboard/shaping.py) |
| Another approval channel, or a second approver | not yet | [config.py](../../src/switchboard/config.py) |
| Configurable server instructions | not yet | [server.py](../../src/switchboard/server.py) |
| A PyPI package or a GitHub release | not yet | [ci.yml](../../.github/workflows/ci.yml) |

## What works

- **Bearer on every path but `/healthz`.** `test_needs_the_bearer_but_healthz_does_not`: `/healthz` answers 200 with no bearer, and `/mcp/<scope>` answers 401 with none or a wrong one.
- **Four tools, or five where writes are allowed.** `test_read_only_scope_lists_four_small_tools_with_the_catalog` gets `search`, `describe`, `read` and `more`, finds the catalog lines in the `search` description, and holds the whole `tools/list` to 8,000 characters or less for its two fake servers. `test_writable_scope_adds_write` finds `write` with `readOnlyHint: false` and `destructiveHint: true`.
- **Search, describe and the catalog lines.** In [test_catalog.py](../../tests/test_catalog.py): `test_search_ranks_by_the_words_that_matter`, `test_tokens_split_camel_and_snake_case`, `test_summarize_takes_the_first_sentence_and_caps_it`, `test_compact_schema_drops_noise_and_keeps_meaning` and `test_render_catalog_with_and_without_counts`. Through the server: `test_search_describe_and_read_with_a_query`, and `test_read_refuses_a_write_tool_and_suggests_for_an_unknown_one` for the "did you mean" hint.
- **Read or write decided by config.** `test_regex_makes_read_and_everything_else_is_write`, `test_annotations_count_only_where_trusted` and `test_write_regex_unknown_server_and_destructive_all_mean_write`. Through the server: `test_read_refuses_a_write_tool_and_suggests_for_an_unknown_one` and `test_write_is_not_offered_on_a_read_only_scope`.
- **Config checks.** In [test_config.py](../../tests/test_config.py): `test_loads_scopes_servers_and_secrets`; `test_rejects_bad_config` with eight bad files (an unknown key, a bad `writes`, a regex that does not compile, no `approval`, an unset variable, a bare-string regex, a non-string regex, a quoted `trust_annotations`); `test_missing_scope_key_names_the_variable`; `test_approval_is_optional_without_writes`; `test_approver_id_must_be_digits`.
- **Result shaping and paging.** All 14 tests in [test_shaping.py](../../tests/test_shaping.py), among them the result ceiling (with the ceiling set to 10,000 characters, a query that doubles the result at every stage is refused in under 2 seconds), list positions kept, and cache ids bound to their scope. Through the server: `test_a_big_result_pages_through_more`, `test_out_of_range_numbers_are_clamped`, `test_an_upstream_tool_error_stays_an_error` and `test_a_result_id_does_not_work_in_another_scope`.
- **Telegram approvals.** In [test_approval.py](../../tests/test_approval.py): `test_approve`, `test_deny_edits_the_message`, `test_timeout_expires_and_a_late_tap_changes_nothing`, `test_replay_and_wrong_user_are_refused`, `test_send_failure_is_unavailable_and_leaves_nothing_pending` and `test_cancelled_wait_settles_expired`.
- **Approval messages that cannot hide arguments.** `test_render_escapes_html_and_caps_args`, `test_a_long_value_cannot_hide_the_arguments_after_it`, `test_invisible_and_bidi_characters_are_shown_as_escapes`, `test_arguments_too_large_to_show_are_refused` and `test_a_write_too_large_to_show_is_never_sent`. Through the server: `test_a_write_too_large_to_show_is_refused_before_anything_runs`.
- **Approved writes: once, shielded, audited.** `test_write_runs_once_after_approval_and_is_audited` checks one upstream call, the client's name in the message and in the audit line, and no arguments in the log. `test_an_approved_write_finishes_and_is_audited_even_if_the_client_gives_up` checks that the write completes after the client gives up, with audit outcomes `running` then `ok` under one approval id. Also `test_write_denied_or_expired_runs_nothing`, `test_a_bad_query_is_refused_before_asking` and `test_a_query_that_cannot_apply_after_the_write_still_reports_the_write`.
- **Upstream session recovery.** In [test_upstream.py](../../tests/test_upstream.py): `test_lists_and_calls_over_one_session`, `test_a_failed_connect_is_an_upstream_error_and_the_next_call_reconnects`, `test_a_dead_session_is_replaced_for_reads_but_writes_are_not_retried`, `test_json_rpc_error_is_reported_and_keeps_the_session`, `test_a_lost_session_error_reconnects_for_reads` (both codes), `test_a_lost_session_on_a_write_is_dropped_but_not_retried`, `test_a_late_failure_on_an_old_session_does_not_drop_the_new_one`, and `test_reads_recover_after_the_gateway_restarts`, which serves a stand-in gateway over HTTP, restarts it, and reads again.
- **Tool catalog refresh.** `test_tool_cache_refreshes_after_ttl_and_keeps_the_last_good_catalog` and `test_counts_is_none_when_upstream_never_answered`.
- **Bot token kept out of errors and logs.** `test_bot_api_errors_and_logs_never_contain_the_token` checks the error text and every log record at DEBUG level.

Two limits apply to these rows. The ranking tests use a catalog of four tools; nothing measures search quality on a large one. The 8,000-character bound holds for two servers, and the catalog grows by one line per server.

## What is partial

- **A write whose upstream call fails after approval.** `write` in [server.py](../../src/switchboard/server.py) reports the result as unknown when the upstream call raises or runs past `WRITE_TIMEOUT_S` (300 seconds). No test drives either path.
- **Through a real agentgateway.** The smoke test runs the image behind agentgateway v1.5.0 with a fake upstream and asserts the tool names, one search and one read ([check.py](../../tests/smoke/check.py)). It covers a read-only scope with no Telegram, and CI does not run it.
- **Container image.** CI builds the image on every run and pushes it on `v*` tags ([ci.yml](../../.github/workflows/ci.yml)). Only the smoke test runs it.
- **Entrypoint and environment variables.** No test runs `main()` in [`__main__.py`](../../src/switchboard/__main__.py): the port variables, the default config path and the log levels are untested. The smoke test starts it with `SWITCHBOARD_CONFIG`.
- **Prometheus metrics.** [metrics.py](../../src/switchboard/metrics.py) defines six series, and no test reads one.
- **Progress while waiting for a tap.** `test_progress_is_reported_while_waiting` covers the approver's callback. No test checks the MCP progress notification a client receives.
- **Catalog lines when the gateway is down at start.** `test_counts_is_none_when_upstream_never_answered` and `test_render_catalog_with_and_without_counts` cover the two halves. No test covers the 20-second wait in `lifespan`.

## What does not exist

Nothing in the repository plans these; it has no roadmap.

- **A recorded measurement of the tool-list saving.** [measure.py](../../scripts/measure.py) prints the size of a route's tool definitions, but the repository records no output. No figure in it backs the README's "instead of hundreds".
- **stdio transport.** [server.py](../../src/switchboard/server.py) serves MCP over Streamable HTTP only.
- **Config reload without a restart.** [`__main__.py`](../../src/switchboard/__main__.py) loads the config once.
- **State that survives a restart.** Pending approvals ([approval.py](../../src/switchboard/approval.py)) and cut results ([shaping.py](../../src/switchboard/shaping.py)) live in memory.
- **Another approval channel, or a second approver.** `Approval` in [config.py](../../src/switchboard/config.py) holds one bot token and one approver id.
- **Configurable server instructions.** The server instructions and the tool descriptions are fixed strings in [server.py](../../src/switchboard/server.py).
- **A PyPI package or a GitHub release.** [ci.yml](../../.github/workflows/ci.yml) publishes only the image, and the name `switchboard` on PyPI belongs to an unrelated project.

## What CI runs

[ci.yml](../../.github/workflows/ci.yml) runs on pushes to `main`, on `v*` tags and on pull requests:

1. `uv sync --frozen`
2. `uv run ruff check .`
3. `uv run pytest`
4. a build of the image for `linux/amd64`
5. on a `v*` tag only, a push of `ghcr.io/egoushka/switchboard:<version>`

It does not run the smoke test.

## Versions and files

- Version 0.1.0 in [pyproject.toml](../../pyproject.toml) and [`__init__.py`](../../src/switchboard/__init__.py), tagged `v0.1.0`.
- Python 3.13 or later. The four runtime dependencies are pinned exactly: `mcp` 2.2.0, `jmespath` 1.1.0, `pyyaml` 6.0.3 and `prometheus-client` 0.26.0; [uv.lock](../../uv.lock) pins the rest.
- The image is `python:3.13-slim` with uv 0.11, runs as user 65534 and exposes ports 8000 and 9109 ([Dockerfile](../../Dockerfile)).
- The smoke test pins agentgateway v1.5.0 by digest ([compose.yaml](../../tests/smoke/compose.yaml)).
- The repository has no license file, no changelog and no architecture decision records. The reasons behind each guard are in the bodies of its [`fix:` commits](https://github.com/Egoushka/switchboard/commits/main).
