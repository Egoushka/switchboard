"""Run switchboard: config from SWITCHBOARD_CONFIG, MCP on :8000, Prometheus on :9109."""

from __future__ import annotations

import logging
import os

import httpx2
import prometheus_client
import uvicorn

from switchboard.approval import Approver, BotAPI
from switchboard.config import load
from switchboard.server import build_app
from switchboard.upstream import Upstream, http_session


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # httpx2 logs every request URL at INFO, and the Telegram URL carries the bot token.
    for name in ("httpx2", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    cfg = load(os.environ.get("SWITCHBOARD_CONFIG", "/config/config.yaml"))
    upstreams = {
        name: Upstream(name, http_session(cfg.upstream + scope.upstream_route, scope.key))
        for name, scope in cfg.scopes.items()
    }
    approver = None
    if cfg.approval is not None:
        api = os.environ.get("SWITCHBOARD_TELEGRAM_API", "https://api.telegram.org")  # or a self-hosted Bot API server
        approver = Approver(BotAPI(cfg.approval.telegram_token, httpx2.AsyncClient(), api), cfg.approval.approver_id)
    prometheus_client.start_http_server(int(os.environ.get("SWITCHBOARD_METRICS_PORT", "9109")))
    uvicorn.run(
        build_app(cfg, upstreams, approver),
        host="0.0.0.0",
        port=int(os.environ.get("SWITCHBOARD_PORT", "8000")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
