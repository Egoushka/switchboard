---
title: "Status and evidence"
description: "What works, what is partial and what does not exist in switchboard v1.0.0, with the test, file or CI step behind each."
order: 6
section: "Project"
---

Each row names its evidence. `works` means a test in this repository covers it. `partial` means part of it has no test, or only the hand-run smoke test covers it. `not yet` means it does not exist.

The 91 tests call no network service outside `127.0.0.1`. They run switchboard in-process or on `127.0.0.1`, with an in-process MCP server standing in for the gateway and a fake standing in for Telegram (`fake_upstream` and `FakeTelegram` in [test_integration.py](../../tests/test_integration.py)). The only run against a real agentgateway is the smoke test in [tests/smoke](../../tests/smoke/): CI runs it on every push, and it uses a fake Telegram Bot API over HTTP.

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
| Bot API calls: long polling and a configurable server URL | works | [test_approval.py](../../tests/test_approval.py), [test_main.py](../../tests/test_main.py) |
| A write whose upstream call fails or times out after approval | works | [test_integration.py](../../tests/test_integration.py) |
| Entrypoint and environment variables | works | [test_main.py](../../tests/test_main.py) |
| Prometheus metrics | works | [test_integration.py](../../tests/test_integration.py) |
| Progress while waiting for a tap | works | [test_integration.py](../../tests/test_integration.py) |
| Catalog lines when the gateway is down or silent at start | works | [test_integration.py](../../tests/test_integration.py) |
| Through a real agentgateway, reads and approved or denied writes | works | [tests/smoke](../../tests/smoke/), [ci.yml](../../.github/workflows/ci.yml) |
| Container image | partial | [Dockerfile](../../Dockerfile), [ci.yml](../../.github/workflows/ci.yml) |
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
- **Bot API calls.** `test_bot_api_long_polls_with_the_offset_and_the_poll_timeout` sends `getUpdates` through the real `BotAPI` against a mock transport. It guards a bug the other tests missed: `getUpdates` raised `TypeError` on every call, because `timeout` was passed twice, so no tap reached switchboard and every write expired. The smoke test found it. `test_bot_api_posts_to_the_configured_server_and_defaults_to_telegram` covers the URL.
- **A write whose upstream call fails or times out after approval.** `test_a_write_whose_upstream_call_raises_after_approval_reports_the_result_as_unknown` and `test_a_write_that_outlasts_the_timeout_after_approval_reports_the_result_as_unknown` (with `WRITE_TIMEOUT_S` set to 0.2 s): the client gets an error that says the write may or may not have happened, the phone message says `result unknown`, and the audit outcomes are `running` then `unknown`.
- **Entrypoint and environment variables.** In [test_main.py](../../tests/test_main.py): defaults (config path, ports 8000 and 9109), overrides of all four variables, the HTTP loggers set to WARNING, a bad config stopping `main()` before anything starts, and a real `python -m switchboard` process that answers `/healthz`, refuses an unauthenticated MCP call and serves `/metrics` on the configured ports.
- **Prometheus metrics.** `test_metrics_count_calls_chars_approvals_and_the_catalog` reads the call, result-character, approval, approval-wait and catalog series after two reads, a failed read, a big read, an approved write and a denied one. `test_metrics_count_upstream_failures` reads the upstream-error series. One gap: a write whose result is unknown adds nothing to `switchboard_calls_total`.
- **Progress while waiting for a tap.** `test_the_client_gets_progress_notifications_while_waiting_for_a_tap`: an MCP client's progress callback receives at least three notifications with a total of 1.0 (the scope's timeout), the message `waiting for Yehor's approval` and rising progress.
- **Catalog lines when the gateway is down or silent at start.** `test_the_catalog_falls_back_to_config_lines_when_the_gateway_is_down_at_start` (config lines without counts, and calls work once the gateway is back), `test_the_gateway_stays_down_without_taking_switchboard_down`, and `test_a_gateway_that_never_answers_is_given_up_on_after_the_startup_wait`, which sets `STARTUP_WAIT_S` to 0.3 s.
- **Through a real agentgateway.** [check.py](../../tests/smoke/check.py) runs against the image behind agentgateway v1.5.0, a fake upstream and a fake Telegram: the tool names of both scopes, a search and a read, a refused write on the read-only scope, one approved write that runs once, one denied write that does not run, and the client name the gateway set in the approval messages. CI runs it in the `smoke` job.

Two limits apply to these rows. The ranking tests use a catalog of four tools; nothing measures search quality on a large one. The 8,000-character bound holds for two servers, and the catalog grows by one line per server.

## What is partial

- **Container image.** CI builds the image on every run and the smoke test runs it, but the push to ghcr.io on `v*` tags ([ci.yml](../../.github/workflows/ci.yml)) has no test, and this repository records no run of it.

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

[ci.yml](../../.github/workflows/ci.yml) runs on pushes to `main`, on `v*` tags and on pull requests, as two jobs:

`smoke`:

1. `uv sync --frozen`
2. a build of the image as `switchboard:dev`
3. `docker compose -f tests/smoke/compose.yaml up -d`
4. `uv run python tests/smoke/check.py`, then the compose logs on failure and `down -v` always

`ci`:

1. `uv sync --frozen`
2. `uv run ruff check .`
3. `uv run pytest`
4. a build of the image for `linux/amd64`
5. on a `v*` tag only, a push of `ghcr.io/egoushka/switchboard:<version>`

## Versions and files

- Version 1.0.0 in [pyproject.toml](../../pyproject.toml) and [`__init__.py`](../../src/switchboard/__init__.py), tagged `v1.0.0`.
- Python 3.13 or later. The four runtime dependencies are pinned exactly: `mcp` 2.2.0, `jmespath` 1.1.0, `pyyaml` 6.0.3 and `prometheus-client` 0.26.0; [uv.lock](../../uv.lock) pins the rest.
- The image is `python:3.13-slim` with uv 0.11, runs as user 65534 and exposes ports 8000 and 9109 ([Dockerfile](../../Dockerfile)).
- The smoke test pins agentgateway v1.5.0 by digest ([compose.yaml](../../tests/smoke/compose.yaml)).
- The repository has an MIT [LICENSE](../../LICENSE) and a [CHANGELOG.md](../../CHANGELOG.md), and no architecture decision records. The reasons behind each guard are in the bodies of its [`fix:` commits](https://github.com/Egoushka/switchboard/commits/main).
