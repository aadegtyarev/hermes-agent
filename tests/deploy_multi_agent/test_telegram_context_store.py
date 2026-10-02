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


# ── Chat title indexing: searchable by name independent of message content ──

def test_upsert_and_search_chat_title_roundtrip(store):
    store.upsert_chat_title("-100700", "WB+Innel (интеграция)")

    hits = store.search_chat_titles("Innel")

    assert [h["chat_id"] for h in hits] == ["-100700"]
    assert hits[0]["title"] == "WB+Innel (интеграция)"


def test_chat_title_findable_with_zero_messages(store):
    """The whole point: a freshly-registered chat has a title but no
    messages yet — must still be findable by name."""
    store.upsert_chat_title("-100700", "WB+Innel (интеграция)")

    assert store.search_chat_titles("Innel") != []
    # No message ever ingested for this chat_id — content search finds nothing,
    # which is fine; title search is what must succeed.
    assert store.search("Innel", "-100700", 50)["messages"] == []


def test_chat_title_search_is_case_and_cyrillic_insensitive(store):
    store.upsert_chat_title("-100500", "Аквариум Групп")

    hits = store.search_chat_titles("АКВАРИУМ")

    assert [h["chat_id"] for h in hits] == ["-100500"]


def test_chat_title_search_restricted_to_given_chat_ids(store):
    store.upsert_chat_title("-100700", "WB+Innel")
    store.upsert_chat_title("-100800", "WB+Innel Backup")

    scoped = store.search_chat_titles("Innel", chat_ids=["-100700"])

    assert [h["chat_id"] for h in scoped] == ["-100700"]


def test_chat_title_search_no_match_returns_empty(store):
    store.upsert_chat_title("-100700", "WB+Innel")

    assert store.search_chat_titles("nonexistent") == []


def test_upsert_chat_title_empty_title_is_a_noop(store):
    store.upsert_chat_title("-100700", "")
    assert store.search_chat_titles("anything") == []


def test_upsert_chat_title_overwrites_on_rename(store):
    store.upsert_chat_title("-100700", "Old Name")
    store.upsert_chat_title("-100700", "New Name")

    assert store.search_chat_titles("Old") == []
    hits = store.search_chat_titles("New")
    assert hits[0]["title"] == "New Name"


def test_set_chat_automatically_indexes_the_title(store):
    store.set_chat("-100700", "client", "WB+Innel (интеграция)", "111", program="integration")

    hits = store.search_chat_titles("Innel")

    assert [h["chat_id"] for h in hits] == ["-100700"]


def test_create_program_automatically_indexes_the_team_chat_title(store):
    store.create_program("integration", "-100500", "111", title="Integration Team")

    hits = store.search_chat_titles("Integration Team")

    assert [h["chat_id"] for h in hits] == ["-100500"]


# ── chat_titles backfill: upgrading onto an already-populated DB ───────────
# chat_titles only gets written going FORWARD (at registration / on ingest).
# A chat registered before this feature shipped has a title sitting unindexed
# in chats_allowed/messages.chat_name — these simulate that pre-upgrade state
# by writing directly via raw SQL (bypassing set_chat()/upsert_chat_title())
# and resetting the migration gate, then re-running init() exactly like a
# redeploy would.

def _reset_chat_titles_backfill_gate(store) -> "sqlite3.Connection":
    import sqlite3
    conn = sqlite3.connect(store._db_path())
    conn.execute("DELETE FROM fts_migration_state WHERE key='chat_titles_backfilled'")
    return conn


def test_init_backfills_a_registered_chats_allowed_title(store):
    conn = _reset_chat_titles_backfill_gate(store)
    conn.execute(
        "INSERT INTO chats_allowed(chat_id,mode,title,added_by,added_ts,program) "
        "VALUES('-100700','client','WB+Innel (интеграция)','111',0,'integration')"
    )
    conn.commit()
    conn.close()

    store.init()

    hits = store.search_chat_titles("Innel")
    assert [h["chat_id"] for h in hits] == ["-100700"]


def test_init_backfills_a_team_chat_title_from_message_history(store):
    """Team chats have no title anywhere EXCEPT message history —
    partner_programs never stored one before this feature existed."""
    conn = _reset_chat_titles_backfill_gate(store)
    conn.execute(
        "INSERT INTO messages(chat_id,message_id,ts,user_id,user_name,chat_type,"
        "chat_name,thread_id,text,reply_to_message_id,reply_to_author) "
        "VALUES('-100500','1',1.0,'u','n','group','Integration Team','','hi','','')"
    )
    conn.commit()
    conn.close()

    store.init()

    hits = store.search_chat_titles("Integration Team")
    assert [h["chat_id"] for h in hits] == ["-100500"]


def test_init_backfill_prefers_the_most_recent_chat_name(store):
    conn = _reset_chat_titles_backfill_gate(store)
    for i, name in enumerate(["Old Group Name", "Renamed Group"]):
        conn.execute(
            "INSERT INTO messages(chat_id,message_id,ts,user_id,user_name,chat_type,"
            "chat_name,thread_id,text,reply_to_message_id,reply_to_author) "
            "VALUES('-100500',?,?,'u','n','group',?,'','hi','','')",
            (str(i), float(i), name),
        )
    conn.commit()
    conn.close()

    store.init()

    assert store.search_chat_titles("Old Group Name") == []
    hits = store.search_chat_titles("Renamed Group")
    assert hits[0]["chat_id"] == "-100500"


def test_init_backfill_registered_title_wins_over_message_history(store):
    conn = _reset_chat_titles_backfill_gate(store)
    conn.execute(
        "INSERT INTO messages(chat_id,message_id,ts,user_id,user_name,chat_type,"
        "chat_name,thread_id,text,reply_to_message_id,reply_to_author) "
        "VALUES('-100700','1',1.0,'u','n','group','Stale Name From History','','hi','','')"
    )
    conn.execute(
        "INSERT INTO chats_allowed(chat_id,mode,title,added_by,added_ts,program) "
        "VALUES('-100700','client','Registered Name','111',0,'integration')"
    )
    conn.commit()
    conn.close()

    store.init()

    assert store.search_chat_titles("Stale Name") == []
    hits = store.search_chat_titles("Registered Name")
    assert hits[0]["chat_id"] == "-100700"


def test_init_backfill_runs_only_once(store):
    """The migration gate must actually gate — re-running init() after the
    one-time backfill already ran must not re-scan chats_allowed/messages
    (harmless either way here since it's idempotent, but confirms the gate
    itself works rather than silently always re-running)."""
    conn = _reset_chat_titles_backfill_gate(store)
    conn.execute(
        "INSERT INTO chats_allowed(chat_id,mode,title,added_by,added_ts,program) "
        "VALUES('-100700','client','First Title','111',0,NULL)"
    )
    conn.commit()
    conn.close()
    store.init()  # consumes the (reset) gate, backfills "First Title"

    # A title change made directly in chats_allowed AFTER the backfill ran
    # must NOT retroactively resync — only upsert_chat_title()/a fresh
    # ingest keeps chat_titles current from here on, same as production.
    import sqlite3
    conn = sqlite3.connect(store._db_path())
    conn.execute("UPDATE chats_allowed SET title='Changed Title' WHERE chat_id='-100700'")
    conn.commit()
    conn.close()

    store.init()  # gate is already consumed — must be a no-op for this chat

    assert store.search_chat_titles("First Title") != []
    assert store.search_chat_titles("Changed Title") == []
