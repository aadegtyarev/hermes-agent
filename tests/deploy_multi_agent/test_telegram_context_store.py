"""Tests for deploy/multi-agent/base/plugins/telegram-context/store.py's
FTS5-backed search — replaced a plain SQL LIKE scan, which had two real
problems for a Russian-language deployment: SQLite's LIKE only case-folds
ASCII ('Hello' matches 'hello', but 'Прошивка' does not match 'прошивка'),
and a substring scan has no notion of word boundaries or Russian's heavy
grammatical inflection (прошивка/прошивку/прошивки differ only in ending).
"""
from __future__ import annotations

import importlib.util
import itertools
import sys
import time
import types
from pathlib import Path

import pytest

_PLUGIN_DIR = (
    Path(__file__).resolve().parents[2]
    / "deploy" / "multi-agent" / "base" / "plugins" / "telegram-context"
)
_counter = itertools.count()


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    mod_name = f"telegram_context_store_under_test_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(mod_name, _PLUGIN_DIR / "store.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    mod.init()
    yield mod
    del sys.modules[mod_name]


def _seed(store, chat_id="C1", n=1, texts=None, base_ts=None, id_prefix=""):
    # id_prefix keeps message_id unique across multiple _seed() calls into the
    # SAME chat_id — messages' PRIMARY KEY is (chat_id, message_id), so reusing
    # plain "0", "1", ... across calls would silently overwrite earlier rows
    # via INSERT OR REPLACE instead of adding new ones.
    base_ts = base_ts if base_ts is not None else time.time() - 3600
    texts = texts or [f"message {i}" for i in range(n)]
    for i, text in enumerate(texts):
        store.add({
            "chat_id": chat_id, "message_id": f"{id_prefix}{i}", "ts": base_ts + i * 60,
            "user_id": "u1", "user_name": "Alice", "chat_type": "group",
            "chat_name": "Test", "thread_id": "", "text": text,
            "reply_to_message_id": "", "reply_to_author": "",
        })


# ── The actual bug being fixed: Cyrillic case-folding ───────────────────────

def test_cyrillic_search_is_case_insensitive(store):
    _seed(store, texts=["Прошивка контроллера обновлена"])

    for query in ("прошивка", "ПРОШИВКА", "Прошивка", "прОшИвка"):
        page = store.search(query, "C1", 50)
        assert len(page["messages"]) == 1, f"query {query!r} should match regardless of case"


def test_ascii_search_still_case_insensitive_as_before(store):
    _seed(store, texts=["Hello world"])
    assert len(store.search("hello", "C1", 50)["messages"]) == 1
    assert len(store.search("HELLO", "C1", 50)["messages"]) == 1


# ── Prefix matching absorbs Russian inflection ──────────────────────────────

def test_prefix_matches_different_grammatical_forms(store):
    _seed(store, texts=[
        "Прошивка контроллера обновлена",
        "Скачал прошивку для платы",
        "прошили куртку у портного",  # different word entirely, must NOT match
    ])

    page = store.search("прошив", "C1", 50)
    texts = {m["text"] for m in page["messages"]}
    assert texts == {"Прошивка контроллера обновлена", "Скачал прошивку для платы"}


def test_multi_word_query_requires_all_words(store):
    _seed(store, texts=[
        "Прошивка контроллера обновлена",
        "Прошивка модуля обновлена",
    ])

    page = store.search("прошивка контроллера", "C1", 50)
    assert [m["text"] for m in page["messages"]] == ["Прошивка контроллера обновлена"]


def test_hyphenated_and_dotted_tokens_split_and_still_match(store):
    _seed(store, texts=["Скачал прошивку для платы wb-map12 версии v2.42"])

    assert len(store.search("wb-map12", "C1", 50)["messages"]) == 1
    assert len(store.search("v2.42", "C1", 50)["messages"]) == 1


# ── Safety: arbitrary input never raises, never behaves like raw FTS5 syntax ─

def test_fts5_special_characters_never_raise(store):
    _seed(store, texts=["normal message here"])

    for hostile in ('"; DROP TABLE messages; --', '"unterminated quote', "col:value", "**", "(a OR b)"):
        page = store.search(hostile, "C1", 50)  # must not raise
        assert isinstance(page["messages"], list)


def test_empty_or_punctuation_only_query_returns_no_results_not_an_error(store):
    _seed(store, texts=["normal message here"])

    assert store.search("", "C1", 50)["messages"] == []
    assert store.search("...", "C1", 50)["messages"] == []
    assert store.count("C1", query="...")["total_count"] == 0


# ── count() must agree with search() (same FTS query, same filters) ────────

def test_count_matches_paginated_search_results(store):
    base = time.time() - 3600
    _seed(store, n=23, texts=[f"прошивка тест {i}" for i in range(23)], base_ts=base)
    _seed(store, chat_id="C1", texts=["не относится к делу"], base_ts=base + 10000, id_prefix="x")

    total = store.count("C1", query="прошивка")["total_count"]
    assert total == 23

    seen = []
    cursor = 0
    while True:
        page = store.search("прошивка", "C1", 7, after_id=cursor)
        seen.extend(m["message_id"] for m in page["messages"])
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        cursor = page["next_cursor"]
    assert len(seen) == len(set(seen)) == total


def test_since_until_window_applies_to_search_too(store):
    base = time.time() - 3600
    _seed(store, n=10, texts=[f"прошивка {i}" for i in range(10)], base_ts=base)

    windowed = store.count(
        "C1", since=base + 3 * 60, until=base + 6 * 60, query="прошивка",
    )
    assert windowed["total_count"] == 4  # indices 3,4,5,6


# ── Backfill: rows written before FTS existed still become searchable ──────

def test_backfill_indexes_rows_inserted_via_raw_sql(store, tmp_path):
    import sqlite3

    conn = sqlite3.connect(store._db_path())
    conn.execute(
        "INSERT INTO messages(chat_id,message_id,ts,user_id,user_name,chat_type,"
        "chat_name,thread_id,text,reply_to_message_id,reply_to_author) "
        "VALUES ('C1','raw',0,'u','n','group','t','','бэкфилл проверка уникальное','','')"
    )
    conn.commit()
    conn.close()

    store.init()  # re-running init() must backfill the FTS index for the new row

    page = store.search("бэкфилл", "C1", 50)
    assert [m["message_id"] for m in page["messages"]] == ["raw"]
