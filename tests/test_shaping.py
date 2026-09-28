import json

import mcp_types as types
import pytest

from switchboard.shaping import QueryError, ResultCache, drop_empty, render, upstream_chars, window


def text(s):
    return types.TextContent(type="text", text=s)


def test_drop_empty_is_recursive_and_keeps_falsy_scalars():
    value = {"a": None, "b": "", "c": [], "d": {}, "e": 0, "f": False, "g": [None, {"h": None}, 1]}
    assert drop_empty(value) == {"e": 0, "f": False, "g": [1]}


def test_structured_content_wins_and_its_text_copy_is_dropped():
    result = types.CallToolResult(content=[text('{"x": 1, "n": null}')], structured_content={"x": 1, "n": None})
    assert render(result) == ('{"x":1}', [])


def test_json_text_is_parsed_and_projected():
    data = {"results": [{"text": "a", "id": 1, "scores": None}, {"text": "b", "id": 2}]}
    assert render(types.CallToolResult(content=[text(json.dumps(data))]), "results[].text") == ('["a","b"]', [])


def test_plain_text_passes_through_and_refuses_a_query():
    result = types.CallToolResult(content=[text("line one"), text("line two")])
    assert render(result) == ("line one\nline two", [])
    with pytest.raises(QueryError, match="plain text"):
        render(result, "x")


def test_a_bad_query_is_reported():
    with pytest.raises(QueryError, match="bad query"):
        render(types.CallToolResult(content=[text("{}")]), "[[[")


def test_non_text_blocks_are_passed_on():
    image = types.ImageContent(type="image", data="aGk=", mime_type="image/png")
    assert render(types.CallToolResult(content=[text("{}"), image]))[1] == [image]


def test_upstream_chars_counts_every_text_and_the_structured_copy():
    result = types.CallToolResult(content=[text('{"x":1}')], structured_content={"x": 1})
    assert upstream_chars(result) == 14


def test_cache_expires_and_evicts_the_oldest():
    now = [0.0]
    cache = ResultCache(ttl_s=10, max_bytes=10, clock=lambda: now[0])
    first = cache.put("aaaaaa", 3)
    second = cache.put("bbbbbb", 3)  # 12 chars > 10: the first goes
    assert cache.get(first) is None and cache.get(second).text == "bbbbbb"
    now[0] = 11
    assert cache.get(second) is None


def test_window_adds_a_trailer_until_the_end():
    assert window("abcdefgh", 0, 3, "r_x") == (
        'abc\n…[truncated: 3 of 8 chars — more(result_id="r_x", offset=3), or narrow with query]'
    )
    assert window("abcdefgh", 6, 3, "r_x") == "gh"
