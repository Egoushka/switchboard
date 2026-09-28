import re

import mcp_types as types

from switchboard.catalog import Index, build, classify, compact_schema, render_catalog, summarize, tokens
from switchboard.config import Server

SERVERS = {
    "oura": Server(about="Oura ring — sleep, readiness, HRV", read=(re.compile("."),)),
    "telegram": Server(
        about="Yehor's Telegram account",
        read=(re.compile(r"^(get|list|search)_"),),
        write=(re.compile("invite"),),
        trust_annotations=True,
    ),
    "hindsight": Server(about="Long-term memory banks", trust_annotations=True),
}


def tool(name, description="", ro=None, destructive=None, schema=None):
    ann = None
    if ro is not None or destructive is not None:
        ann = types.ToolAnnotations(read_only_hint=ro, destructive_hint=destructive)
    return types.Tool(name=name, description=description, input_schema=schema or {"type": "object"}, annotations=ann)


def test_regex_makes_read_and_everything_else_is_write():
    assert classify(tool("telegram_get_chats"), SERVERS) == ("telegram", "read", False)
    assert classify(tool("telegram_send_message"), SERVERS) == ("telegram", "write", False)


def test_annotations_count_only_where_trusted():
    assert classify(tool("hindsight_recall", ro=True), SERVERS)[1] == "read"
    assert classify(tool("hindsight_retain", ro=False), SERVERS)[1] == "write"
    untrusted = {"oura": Server(about="x")}
    assert classify(tool("oura_sleep", ro=True), untrusted)[1] == "write"


def test_write_regex_unknown_server_and_destructive_all_mean_write():
    assert classify(tool("telegram_get_invite_link", ro=True), SERVERS)[1] == "write"
    assert classify(tool("pangolin_list_sites", ro=True), SERVERS) == ("pangolin", "write", False)
    assert classify(tool("oura_delete_everything", destructive=True), SERVERS) == ("oura", "write", True)


def test_summarize_takes_the_first_sentence_and_caps_it():
    assert summarize("Recall memories. Uses a bank.") == "Recall memories."
    assert summarize(None) == ""
    capped = summarize("x" * 300)
    assert len(capped) == 140 and capped.endswith("…")


def test_compact_schema_drops_noise_and_keeps_meaning():
    schema = {
        "$schema": "x",
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "properties": {
            "bank_id": {"title": "Bank Id", "type": "string", "description": "which bank", "default": None},
            "limit": {"title": "How many", "type": "integer"},
        },
    }
    assert compact_schema(schema) == {
        "type": "object",
        "properties": {
            "bank_id": {"type": "string", "description": "which bank"},
            "limit": {"title": "How many", "type": "integer"},
        },
    }


def test_search_ranks_by_the_words_that_matter():
    tools = build(
        [
            tool("oura_night_detail", "One night's sleep: stages, HRV, heart rate."),
            tool("telegram_send_message", "Send a message to a chat."),
            tool("telegram_get_chats", "List chats."),
            tool("hindsight_recall", "Recall memories from a bank.", ro=True),
        ],
        SERVERS,
    )
    index = Index(tools, SERVERS)
    assert index.search("how did I sleep last night")[0].name == "oura_night_detail"
    assert index.search("send telegram message")[0].name == "telegram_send_message"
    assert index.search("what memories do I have")[0].name == "hindsight_recall"
    assert [t.name for t in index.search("", server="telegram")] == ["telegram_send_message", "telegram_get_chats"]
    assert index.search("") == []
    assert index.search("zzzz nothing") == []
    assert len(index.search("chat", limit=1)) == 1


def test_tokens_split_camel_and_snake_case():
    assert tokens("getChatHistory list_Messages") == ["get", "chat", "history", "list", "messages"]


def test_render_catalog_with_and_without_counts():
    assert render_catalog({"oura": 7, "jarvis": 1}, SERVERS) == (
        "- jarvis (1 tools)\n- oura (7 tools): Oura ring — sleep, readiness, HRV"
    )
    assert render_catalog(None, SERVERS).splitlines()[0] == "- hindsight: Long-term memory banks"
