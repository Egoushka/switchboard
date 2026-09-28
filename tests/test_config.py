import pytest

from switchboard.config import ConfigError, load

ENV = {"SB_TOKEN": "t0k", "KEY_HOMELAB": "k1", "TG_TOKEN": "123:abc", "TG_ME": "42"}
APPROVAL = "approval: {telegram_token_env: TG_TOKEN, approver_id_env: TG_ME, timeout_s: 45}\n"
BASE = (
    "upstream: http://agentgateway:3000/\n"
    "ingress_token_env: SB_TOKEN\n"
    + APPROVAL
    + "scopes:\n"
    "  homelab: {upstream_route: /mcp/homelab, key_env: KEY_HOMELAB, writes: approve}\n"
    "  homelab-ro: {upstream_route: /mcp/homelab, key_env: KEY_HOMELAB, writes: off, approval_timeout_s: 30}\n"
    "servers:\n"
    '  oura: {about: "Oura ring", read: ["."]}\n'
    '  telegram: {about: "Telegram", read: ["^(get|list)_"], write: ["invite"], trust_annotations: true}\n'
)


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return str(path)


def test_loads_scopes_servers_and_secrets(tmp_path):
    cfg = load(write(tmp_path, BASE), ENV)
    assert cfg.upstream == "http://agentgateway:3000"
    assert cfg.ingress_token == "t0k"
    assert cfg.approval.approver_id == 42 and cfg.approval.telegram_token == "123:abc"
    assert cfg.scopes["homelab"].writes and cfg.scopes["homelab"].approval_timeout_s == 45
    assert not cfg.scopes["homelab-ro"].writes  # a bare `off` is YAML False
    assert cfg.scopes["homelab-ro"].approval_timeout_s == 30
    telegram = cfg.servers["telegram"]
    assert telegram.trust_annotations
    assert telegram.read[0].search("get_chat") and telegram.write[0].search("get_invite_link")
    assert cfg.results.max_chars == 6000


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (BASE + "extra: 1\n", "unknown key"),
        (BASE.replace("writes: approve", "writes: maybe"), "must be 'approve' or 'off'"),
        (BASE.replace('read: ["^(get|list)_"]', 'read: ["(unclosed"]'), r"servers\.telegram\.read"),
        (BASE.replace(APPROVAL, ""), "approval: required"),
        (BASE.replace("TG_ME, timeout", "TG_BAD, timeout"), "TG_BAD"),
    ],
)
def test_rejects_bad_config(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        load(write(tmp_path, text), ENV)


def test_missing_scope_key_names_the_variable(tmp_path):
    with pytest.raises(ConfigError, match="KEY_HOMELAB"):
        load(write(tmp_path, BASE), {k: v for k, v in ENV.items() if k != "KEY_HOMELAB"})


def test_approval_is_optional_without_writes(tmp_path):
    text = BASE.replace(APPROVAL, "").replace("writes: approve", "writes: off")
    cfg = load(write(tmp_path, text), ENV)
    assert cfg.approval is None
    assert cfg.scopes["homelab"].approval_timeout_s == 50.0


def test_approver_id_must_be_digits(tmp_path):
    with pytest.raises(ConfigError, match="digits"):
        load(write(tmp_path, BASE), {**ENV, "TG_ME": "@yehor"})
