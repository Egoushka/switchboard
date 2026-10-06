import functools
import json
import logging

import anyio
import httpx2
import pytest

from switchboard.approval import ApprovalUnavailable, Approver, BotAPI, Request, TooLargeToShow, render

ME = 42


class FakeTelegram:
    def __init__(self, fail_send=False):
        self.sent, self.edits, self.answers, self.fail_send = [], [], [], fail_send

    async def send(self, chat_id, text, buttons):
        if self.fail_send:
            raise RuntimeError("telegram down")
        self.sent.append((chat_id, text, buttons))
        return len(self.sent)

    async def edit(self, chat_id, message_id, text):
        self.edits.append((message_id, text))

    async def answer(self, callback_id, text):
        self.answers.append(text)

    async def updates(self, offset, timeout):
        await anyio.sleep_forever()


def tap(data, user=ME):
    return {"update_id": 7, "callback_query": {"id": "cb", "from": {"id": user}, "data": data}}


async def ask(approver, out, **overrides):
    params = {
        "scope": "homelab", "client": "laptop", "tool": "notes_add", "args": {"text": "hi"}, "reason": "test",
        "destructive": False, "timeout_s": 2,
    }
    params.update(overrides)
    out.append(await approver.ask(**params))


async def request_id(fake, n=1):
    with anyio.fail_after(2):
        while len(fake.sent) < n:
            await anyio.sleep(0.01)
    return fake.sent[n - 1][2][0][1].partition(":")[2]


@pytest.mark.anyio
async def test_approve():
    fake, out = FakeTelegram(), []
    approver = Approver(fake, ME)
    async with anyio.create_task_group() as tg:
        tg.start_soon(ask, approver, out)
        await approver.handle(tap(f"a:{await request_id(fake)}"))
    assert out[0].decision == "approved" and fake.answers == ["approved"]
    assert fake.sent[0][0] == ME


@pytest.mark.anyio
async def test_deny_edits_the_message():
    fake, out = FakeTelegram(), []
    approver = Approver(fake, ME)
    async with anyio.create_task_group() as tg:
        tg.start_soon(ask, approver, out)
        await approver.handle(tap(f"d:{await request_id(fake)}"))
    assert out[0].decision == "denied"
    assert "denied" in fake.edits[-1][1]


@pytest.mark.anyio
async def test_timeout_expires_and_a_late_tap_changes_nothing():
    fake, out = FakeTelegram(), []
    approver = Approver(fake, ME)
    async with anyio.create_task_group() as tg:
        tg.start_soon(functools.partial(ask, approver, out, timeout_s=0.2))
        rid = await request_id(fake)
    assert out[0].decision == "expired" and "expired" in fake.edits[-1][1]
    await approver.handle(tap(f"a:{rid}"))
    assert out[0].decision == "expired" and fake.answers == ["expired or unknown"]


@pytest.mark.anyio
async def test_replay_and_wrong_user_are_refused():
    fake, out = FakeTelegram(), []
    approver = Approver(fake, ME)
    async with anyio.create_task_group() as tg:
        tg.start_soon(ask, approver, out)
        rid = await request_id(fake)
        await approver.handle(tap(f"a:{rid}", user=666))
        await approver.handle(tap(f"a:{rid}"))
        await approver.handle(tap(f"a:{rid}"))
    assert out[0].decision == "approved"
    assert fake.answers == ["expired or unknown", "approved", "expired or unknown"]


@pytest.mark.anyio
async def test_send_failure_is_unavailable_and_leaves_nothing_pending():
    approver = Approver(FakeTelegram(fail_send=True), ME)
    with pytest.raises(ApprovalUnavailable):
        await ask(approver, [])
    assert approver._pending == {}


@pytest.mark.anyio
async def test_progress_is_reported_while_waiting():
    fake, out, waits = FakeTelegram(), [], []
    approver = Approver(fake, ME, progress_every_s=0.05)

    async def on_wait():
        waits.append(1)

    async with anyio.create_task_group() as tg:
        tg.start_soon(functools.partial(ask, approver, out, timeout_s=0.3, on_wait=on_wait))
    assert out[0].decision == "expired" and len(waits) >= 3


@pytest.mark.anyio
async def test_cancelled_wait_settles_expired():
    fake, out = FakeTelegram(), []
    approver = Approver(fake, ME)
    async with anyio.create_task_group() as tg:
        tg.start_soon(ask, approver, out)
        rid = await request_id(fake)
        tg.cancel_scope.cancel()
    assert approver._pending == {} and "expired" in fake.edits[-1][1]
    await approver.handle(tap(f"a:{rid}"))
    assert fake.answers == ["expired or unknown"]


def test_render_escapes_html_and_caps_args():
    req = Request(id="x", scope="homelab", client="<b>", tool="notes_add", args={"t": "<i>&" + "y" * 3000},
                  reason="because <3", destructive=True)
    text = render(req)
    assert "&lt;b&gt;" in text and "&lt;i&gt;&amp;" in text and "because &lt;3" in text
    assert "🔴" in text and len(text) < 2500


@pytest.mark.anyio
async def test_bot_api_errors_and_logs_never_contain_the_token(caplog):
    token = "123456:SECRET-TOKEN"

    def ok(request):
        return httpx2.Response(200, json={"ok": True, "result": {"message_id": 5}})

    def down(request):
        raise httpx2.ConnectError("boom", request=request)

    caplog.set_level(logging.DEBUG)
    good = BotAPI(token, httpx2.AsyncClient(transport=httpx2.MockTransport(ok)))
    assert await good.send(ME, "hi", [("Approve", "a:x")]) == 5  # httpx2 logs this request's URL at INFO
    bad = BotAPI(token, httpx2.AsyncClient(transport=httpx2.MockTransport(down)))
    with pytest.raises(ApprovalUnavailable) as raised:
        await bad.send(ME, "hi", [("Approve", "a:x")])
    assert token not in str(raised.value) and raised.value.__cause__ is None
    assert all(token not in r.getMessage() for r in caplog.records)


def request(args, tool="telegram_send_message", reason="r"):
    return Request(id="x", scope="homelab", client="laptop", tool=tool, args=args, reason=reason, destructive=False)


def test_a_long_value_cannot_hide_the_arguments_after_it():
    text = render(request({"message": "x" * 1600, "chat_id": "@stranger"}))
    assert "&quot;chat_id&quot;: &quot;@stranger&quot;" in text and "…(+1400)" in text


def test_invisible_and_bidi_characters_are_shown_as_escapes():
    text = render(request({"to": "ok\u202egnp.exe"}, tool="notes\u200b_add", reason="fine\u2066"))
    assert "\u202e" not in text and "\u200b" not in text and "\u2066" not in text
    assert "\\u202e" in text and "\\u200b" in text and "\\u2066" in text


def test_arguments_too_large_to_show_are_refused():
    with pytest.raises(TooLargeToShow, match="too large to show"):
        render(request({f"k{i}": "v" * 150 for i in range(40)}))


@pytest.mark.anyio
async def test_a_write_too_large_to_show_is_never_sent():
    fake = FakeTelegram()
    approver = Approver(fake, ME)
    with pytest.raises(ApprovalUnavailable, match="too large to show"):
        await ask(approver, [], args={f"k{i}": "v" * 150 for i in range(40)})
    assert fake.sent == [] and approver._pending == {}


@pytest.mark.anyio
async def test_bot_api_posts_to_the_configured_server_and_defaults_to_telegram():
    urls = []

    def ok(request):
        urls.append(str(request.url))
        return httpx2.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(ok))
    await BotAPI("tok", client).send(ME, "hi", [("Approve", "a:x")])
    await BotAPI("tok", client, api="http://bot-api:8081/").send(ME, "hi", [("Approve", "a:x")])
    assert urls == ["https://api.telegram.org/bottok/sendMessage", "http://bot-api:8081/bottok/sendMessage"]


@pytest.mark.anyio
async def test_bot_api_long_polls_with_the_offset_and_the_poll_timeout():
    bodies = []

    def ok(request):
        bodies.append(json.loads(request.content))
        return httpx2.Response(200, json={"ok": True, "result": [{"update_id": 7}]})

    api = BotAPI("tok", httpx2.AsyncClient(transport=httpx2.MockTransport(ok)))
    assert await api.updates(7, 50) == [{"update_id": 7}]
    assert bodies == [{"offset": 7, "timeout": 50, "allowed_updates": ["callback_query"]}]
