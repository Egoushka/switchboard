import logging
import os
import socket
import subprocess
import sys
import time

import httpx2
import prometheus_client
import pytest
import uvicorn

from switchboard import __main__ as entry
from switchboard.config import load

CONFIG = """\
upstream: http://127.0.0.1:1
ingress_token_env: SB_TOKEN
scopes:
  ro: {upstream_route: /mcp/ro, key_env: SB_KEY, writes: "off"}
servers:
  oura: {about: "Oura ring"}
"""
ENV = {"SB_TOKEN": "t", "SB_KEY": "k"}


@pytest.fixture
def launched(monkeypatch, tmp_path):
    """main() with the two servers stubbed: records what it would start."""
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(app=app, uvicorn=kw))
    monkeypatch.setattr(prometheus_client, "start_http_server", lambda port: seen.update(metrics=port))
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG)
    monkeypatch.setattr(entry, "load", lambda p: seen.update(config=p) or load(str(path)))
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    for key in ("SWITCHBOARD_CONFIG", "SWITCHBOARD_PORT", "SWITCHBOARD_METRICS_PORT"):
        monkeypatch.delenv(key, raising=False)
    return seen


def test_defaults(launched):
    entry.main()
    assert launched["config"] == "/config/config.yaml"
    assert launched["metrics"] == 9109
    assert launched["uvicorn"]["port"] == 8000 and launched["uvicorn"]["host"] == "0.0.0.0"
    assert launched["uvicorn"]["log_level"] == "warning"


def test_environment_overrides(launched, monkeypatch):
    monkeypatch.setenv("SWITCHBOARD_CONFIG", "/etc/sb.yaml")
    monkeypatch.setenv("SWITCHBOARD_PORT", "18000")
    monkeypatch.setenv("SWITCHBOARD_METRICS_PORT", "19109")
    entry.main()
    assert launched["config"] == "/etc/sb.yaml"
    assert launched["metrics"] == 19109 and launched["uvicorn"]["port"] == 18000


def test_http_client_loggers_are_quiet_because_the_telegram_url_carries_the_bot_token(launched):
    for name in ("httpx2", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    entry.main()
    assert [logging.getLogger(n).level for n in ("httpx2", "httpx", "httpcore")] == [logging.WARNING] * 3


def test_a_bad_config_stops_main_before_anything_starts(launched, monkeypatch):
    monkeypatch.delenv("SB_KEY")
    with pytest.raises(Exception, match="SB_KEY"):
        entry.main()
    assert "metrics" not in launched and "uvicorn" not in launched


WRITES = CONFIG.replace('writes: "off"}', 'writes: approve}') + "approval: {telegram_token_env: SB_TG, approver_id_env: SB_ME}\n"


def test_the_telegram_api_url_comes_from_the_environment(launched, monkeypatch, tmp_path):
    path = tmp_path / "writes.yaml"
    path.write_text(WRITES)
    monkeypatch.setattr(entry, "load", lambda p: load(str(path)))
    monkeypatch.setenv("SB_TG", "tok")
    monkeypatch.setenv("SB_ME", "42")
    apis = []
    monkeypatch.setattr(entry, "BotAPI", lambda token, http, api: apis.append((token, api)))
    monkeypatch.delenv("SWITCHBOARD_TELEGRAM_API", raising=False)
    entry.main()
    monkeypatch.setenv("SWITCHBOARD_TELEGRAM_API", "http://bot-api:8081")
    entry.main()
    assert apis == [("tok", "https://api.telegram.org"), ("tok", "http://bot-api:8081")]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_entrypoint_serves_mcp_health_and_metrics_on_the_configured_ports(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG)
    port, metrics_port = free_port(), free_port()
    env = {**os.environ, **ENV, "SWITCHBOARD_CONFIG": str(path), "SWITCHBOARD_PORT": str(port),
           "SWITCHBOARD_METRICS_PORT": str(metrics_port)}
    proc = subprocess.Popen([sys.executable, "-m", "switchboard"], env=env, stderr=subprocess.PIPE, text=True)
    try:
        with httpx2.Client(timeout=2) as http:
            for _ in range(100):
                try:
                    health = http.get(f"http://127.0.0.1:{port}/healthz")
                    break
                except httpx2.TransportError:
                    assert proc.poll() is None, proc.stderr.read()
                    time.sleep(0.1)
            else:
                pytest.fail("switchboard did not start")
            assert health.status_code == 200 and health.text == "ok"
            assert http.post(f"http://127.0.0.1:{port}/mcp/ro", json={}).status_code == 401
            body = http.get(f"http://127.0.0.1:{metrics_port}/metrics").text
        assert "switchboard_approval_wait_seconds" in body
    finally:
        proc.terminate()
        proc.wait(10)
