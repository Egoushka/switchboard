"""Fake Telegram Bot API for the smoke test: taps Approve, or Deny when the request shows "deny-me".

GET /log returns every message sent and every edit made, so check.py can read what the phone would show.
"""

import anyio
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

APPROVER_ID = 4242
messages: list[str] = []
edits: list[str] = []
updates: list[dict] = []
arrived = anyio.Event()


def result(value) -> JSONResponse:
    return JSONResponse({"ok": True, "result": value})


async def bot(request: Request) -> JSONResponse:
    global arrived
    method, body = request.path_params["method"], await request.json()
    if method == "sendMessage":
        messages.append(body["text"])
        rid = body["reply_markup"]["inline_keyboard"][0][0]["callback_data"].partition(":")[2]  # the Approve button
        tap = "d" if "deny-me" in body["text"] else "a"
        updates.append(
            {"update_id": len(updates) + 1, "callback_query": {"id": f"cb{len(updates)}", "from": {"id": APPROVER_ID}, "data": f"{tap}:{rid}"}}
        )
        arrived.set()
        return result({"message_id": len(messages)})
    if method == "editMessageText":
        edits.append(body["text"])
        return result(True)
    if method == "getUpdates":
        fresh = [u for u in updates if u["update_id"] >= body["offset"]]
        if not fresh:
            with anyio.move_on_after(body["timeout"]):
                await arrived.wait()
            arrived = anyio.Event()
            fresh = [u for u in updates if u["update_id"] >= body["offset"]]
        return result(fresh)
    return result(True)  # answerCallbackQuery


async def log(_request: Request) -> JSONResponse:
    return JSONResponse({"messages": messages, "edits": edits})


app = Starlette(routes=[Route("/bot{token}/{method}", bot, methods=["POST"]), Route("/log", log)])
uvicorn.run(app, host="0.0.0.0", port=8081, log_level="warning")
