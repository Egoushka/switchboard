import json
import sys
import time

import mcp_types as types
import pytest

from switchboard.shaping import (
    QueryError,
    ResultCache,
    ResultTooLarge,
    drop_empty,
    render,
    upstream_chars,
    window,
)


def text(s):
    return types.TextContent(type="text", text=s)


def test_drop_empty_is_recursive_and_keeps_falsy_scalars():
    value = {"a": None, "b": "", "c": [], "d": {}, "e": 0, "f": False, "g": [None, {"h": None}, 1]}
    assert drop_empty(value) == {"e": 0, "f": False, "g": [None, {}, 1]}


def test_drop_empty_keeps_every_list_element_so_positions_hold():
    rows = {"columns": ["date", "amount", "note"], "rows": [["2026-09-01", None, "rent"]]}
    assert drop_empty(rows) == rows


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
    size = sys.getsizeof("aaaaaa")
    cache = ResultCache(ttl_s=10, max_bytes=size + size // 2, clock=lambda: now[0])
    first = cache.put("aaaaaa", 3, "homelab")
    second = cache.put("bbbbbb", 3, "homelab")  # two entries exceed the budget: the first goes
    assert cache.get(first, "homelab") is None and cache.get(second, "homelab").text == "bbbbbb"
    now[0] = 11
    assert cache.get(second, "homelab") is None


def test_cache_counts_real_memory_not_chars():
    size = sys.getsizeof("я" * 60)  # two bytes per char in CPython, plus the object header
    cache = ResultCache(ttl_s=10, max_bytes=size + 10)
    first = cache.put("я" * 60, 10, "homelab")
    cache.put("я" * 60, 10, "homelab")
    assert cache.get(first, "homelab") is None


def test_cache_ids_are_bound_to_their_scope():
    cache = ResultCache(ttl_s=10, max_bytes=10_000)
    rid = cache.put("from homelab", 5, "homelab")
    assert cache.get(rid, "omi-desktop") is None and cache.get(rid, "homelab").text == "from homelab"


def test_a_result_over_the_ceiling_is_refused_without_building_it():
    bomb = "|".join(["{a:@,b:@}"] * 40)  # doubles the serialized size with every stage
    started = time.monotonic()
    with pytest.raises(ResultTooLarge, match="narrow"):
        render(types.CallToolResult(content=[text('{"k": "v"}')]), bomb, ceiling=10_000)
    assert time.monotonic() - started < 2


def test_plain_text_over_the_ceiling_is_cut_with_a_note():
    out, _ = render(types.CallToolResult(content=[text("x" * 50)]), ceiling=20)
    assert out.startswith("x" * 20) and "cut at 20 chars" in out


def test_window_adds_a_trailer_until_the_end():
    assert window("abcdefgh", 0, 3, "r_x") == (
        'abc\n…[truncated: 3 of 8 chars — more(result_id="r_x", offset=3), or narrow with query]'
    )
    assert window("abcdefgh", 6, 3, "r_x") == "gh"
