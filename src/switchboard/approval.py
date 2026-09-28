"""Per-call approval of writes on Yehor's phone, through a dedicated Telegram bot."""

from __future__ import annotations

import copy
import html
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import anyio
import httpx2

log = logging.getLogger(__name__)
# httpx2 logs every request URL at INFO, and a Bot API URL carries the token.
logging.getLogger("httpx2").setLevel(logging.WARNING)


class ApprovalUnavailable(Exception):
    pass


class Telegram(Protocol):
    async def send(self, chat_id: int, text: str, buttons: list[tuple[str, str]]) -> int: ...
    async def edit(self, chat_id: int, message_id: int, text: str) -> None: ...
    async def answer(self, callback_id: str, text: str) -> None: ...
    async def updates(self, offset: int, timeout: int) -> list[dict[str, Any]]: ...


class BotAPI:
    """The four Bot API calls switchboard needs. Errors never carry the URL: it contains the token."""

    def __init__(self, token: str, http: httpx2.AsyncClient):
        self._base, self._http = f"https://api.telegram.org/bot{token}/", http

    async def _call(self, method: str, timeout: float = 15, **params: Any) -> Any:
        try:
            response = await self._http.post(self._base + method, json=params, timeout=timeout)
            body = response.json()
        except (httpx2.HTTPError, ValueError) as e:
            raise ApprovalUnavailable(f"telegram {method}: {type(e).__name__}") from None
        if not body.get("ok"):
            raise ApprovalUnavailable(f"telegram {method}: {body.get('description', response.status_code)}")
        return body["result"]

    async def send(self, chat_id: int, text: str, buttons: list[tuple[str, str]]) -> int:
        markup = {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in buttons]]}
        message = await self._call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=markup)
        return message["message_id"]

    async def edit(self, chat_id: int, message_id: int, text: str) -> None:
        await self._call("editMessageText", chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML")

    async def answer(self, callback_id: str, text: str) -> None:
        await self._call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    async def updates(self, offset: int, timeout: int) -> list[dict[str, Any]]:
        return await self._call(
            "getUpdates", timeout + 10, offset=offset, timeout=timeout, allowed_updates=["callback_query"]
        )


@dataclass
class Request:
    id: str
    scope: str
    client: str
    tool: str
    args: dict[str, Any]
    reason: str
    destructive: bool
    decision: str | None = None  # approved | denied | expired
    message_id: int | None = None
    settled: anyio.Event = field(default_factory=anyio.Event)


def render(req: Request) -> str:
    args = json.dumps(req.args, ensure_ascii=False, indent=1)
    if len(args) > 1500:
        args = args[:1500] + "…"
    flag = " 🔴 destructive" if req.destructive else ""
    return (
        f"<b>Write request</b> · {html.escape(req.scope)} · {html.escape(req.client)}\n"
        f"<b>{html.escape(req.tool)}</b>{flag}\n"
        f"<i>{html.escape(req.reason[:500])}</i>\n"
        f"<pre>{html.escape(args)}</pre>"
    )


class Approver:
    def __init__(self, api: Telegram, approver_id: int, progress_every_s: float = 15):
        self._api, self._me, self._every = api, approver_id, progress_every_s
        self._pending: dict[str, Request] = {}
        self._offset = 0

    def _settle(self, req: Request, decision: str) -> bool:
        """The first of approve, deny or deadline wins; everything later sees a settled request."""
        if req.decision is not None:
            return False
        req.decision = decision
        self._pending.pop(req.id, None)
        req.settled.set()
        return True

    async def ask(
        self,
        *,
        scope: str,
        client: str,
        tool: str,
        args: dict[str, Any],
        reason: str,
        destructive: bool,
        timeout_s: float,
        on_wait: Callable[[], Awaitable[None]] | None = None,
    ) -> Request:
        req = Request(
            id=secrets.token_urlsafe(12), scope=scope, client=client, tool=tool,
            args=copy.deepcopy(args), reason=reason, destructive=destructive,
        )
        self._pending[req.id] = req
        try:
            req.message_id = await self._api.send(
                self._me, render(req), [("Approve", f"a:{req.id}"), ("Deny", f"d:{req.id}")]
            )
        except Exception as e:  # noqa: BLE001 - any send failure must fail closed
            self._pending.pop(req.id, None)
            raise ApprovalUnavailable(str(e)) from None
        try:
            with anyio.move_on_after(timeout_s):
                while not req.settled.is_set():
                    with anyio.move_on_after(self._every):
                        await req.settled.wait()
                    if not req.settled.is_set() and on_wait is not None:
                        try:
                            await on_wait()
                        except Exception:
                            log.debug("progress notification failed", exc_info=True)
        finally:
            if self._settle(req, "expired"):
                with anyio.move_on_after(5, shield=True):
                    await self.report(req, "⌛ expired — nothing was executed")
        return req

    async def report(self, req: Request, outcome: str) -> None:
        if req.message_id is None:
            return
        try:
            await self._api.edit(self._me, req.message_id, f"{render(req)}\n\n{html.escape(outcome)}")
        except Exception:  # noqa: BLE001 - the decision stands even if the message edit fails
            log.warning("could not update the approval message for %s", req.tool)

    async def handle(self, update: dict[str, Any]) -> None:
        cq = update.get("callback_query")
        if not cq:
            return
        action, _, rid = str(cq.get("data", "")).partition(":")
        req = self._pending.get(rid)
        decision = "approved" if action == "a" else "denied"
        valid = (cq.get("from") or {}).get("id") == self._me and req is not None and action in ("a", "d")
        if not valid or not self._settle(req, decision):
            await self._answer(cq, "expired or unknown")
            return
        await self._answer(cq, decision)
        if decision == "denied":
            await self.report(req, "❌ denied — nothing was executed")

    async def _answer(self, cq: dict[str, Any], text: str) -> None:
        try:
            await self._api.answer(cq["id"], text)
        except Exception:  # noqa: BLE001 - an unanswered callback only leaves a spinner on the phone
            log.warning("could not answer a Telegram callback")

    async def poll_forever(self) -> None:
        while True:
            try:
                updates = await self._api.updates(self._offset, 50)
            except Exception as e:  # noqa: BLE001 - the poll loop must outlive any Telegram failure
                log.warning("telegram getUpdates failed: %s", e)
                await anyio.sleep(5)
                continue
            for update in updates:
                self._offset = max(self._offset, update["update_id"] + 1)
                await self.handle(update)
