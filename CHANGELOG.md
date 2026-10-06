# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-10-06

### Added

- `SWITCHBOARD_TELEGRAM_API`: the Bot API server to call, `https://api.telegram.org` by default.
- Tests for a write whose upstream call fails or times out after approval, `main()` and its environment variables, every Prometheus series, the MCP progress notification, and a gateway that is down or silent at start.
- The smoke test covers a scope with writes against a fake Telegram, and CI runs it on every push and pull request.
- `LICENSE` (MIT).

### Fixed

- Telegram approvals never completed: `BotAPI.updates` passed `timeout` twice to `_call`, so every `getUpdates` raised `TypeError` and no tap reached switchboard. Every write expired. Unit tests missed it because they replace the whole `BotAPI`.

## [0.1.0] - 2026-09-29

First tagged version: the `search`, `describe`, `read`, `write` and `more` tools per scope behind agentgateway, Telegram approval of writes with an audit line, result shaping and paging, Prometheus metrics, and a container image.
