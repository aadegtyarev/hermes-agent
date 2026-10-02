"""Tests for chat-title-aware telegram_search (tools.py + __init__.py glue).

The store-level mechanics (store.upsert_chat_title/search_chat_titles,
set_chat/create_program auto-indexing) are covered in
test_telegram_context_store.py. This file covers the two integration points:
- handle_telegram_search surfacing title matches as 'matched_chats'.
- _ingest keeping the title index fresh for every observed chat (not just
  explicitly /hermes_*-registered ones).
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_PLUGIN_DIR = (
    Path(__file__).resolve().parents[2]
    / "deploy" / "multi-agent" / "base" / "plugins" / "telegram-context"
)
_counter = itertools.count()


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    pkg_name = f"telegram_context_title_search_under_test_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(
        pkg_name, _PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(_PLUGIN_DIR)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = pkg_name
    mod.__path__ = [str(_PLUGIN_DIR)]
    sys.modules[pkg_name] = mod
    spec.loader.exec_module(mod)
    mod.store.init()

    yield mod

    for key in list(sys.modules):
        if key == pkg_name or key.startswith(pkg_name + "."):
            del sys.modules[key]


def _group_message(text, *, chat_id="-100700", from_user_id="222", chat_name="WB+Innel (интеграция)"):
    return SimpleNamespace(
        message_id="1",
        text=text,
        source=SimpleNamespace(
            platform="telegram", chat_id=chat_id, user_id=from_user_id,
            user_name="Alice", chat_type="group", chat_name=chat_name,
            thread_id=None,
        ),
        reply_to_message_id=None,
        reply_to_author_name=None,
    )


# ── telegram_search surfaces title matches ───────────────────────────────────

def test_telegram_search_finds_a_chat_by_title_with_no_messages(plugin):
    plugin.store.set_chat("-100700", "client", "WB+Innel (интеграция)", "111", program="integration")

    result = json.loads(plugin.T.handle_telegram_search({"query": "Innel"}, session_id="s"))

    assert result["matched_chats"] == [{"chat_id": "-100700", "title": "WB+Innel (интеграция)"}]
    assert result["matches"] == []  # no message content matched — expected, not an error


def test_telegram_search_combines_title_and_content_matches(plugin):
    plugin.store.set_chat("-100700", "client", "WB+Innel (интеграция)", "111", program="integration")
    plugin.store.add({
        "chat_id": "-100700", "message_id": "5", "ts": 1.0, "user_id": "222",
        "user_name": "Alice", "chat_type": "group", "chat_name": "WB+Innel (интеграция)",
        "thread_id": "", "text": "видел последний релиз Innel", "reply_to_message_id": "",
        "reply_to_author": "",
    })

    result = json.loads(plugin.T.handle_telegram_search({"query": "Innel"}, session_id="s"))

    assert result["matched_chats"] == [{"chat_id": "-100700", "title": "WB+Innel (интеграция)"}]
    assert result["count"] == 1
    assert result["matches"][0]["chat_id"] == "-100700"
    assert result["matches"][0]["chat_name"] == "WB+Innel (интеграция)"


def test_telegram_search_count_only_also_includes_matched_chats(plugin):
    plugin.store.set_chat("-100700", "client", "WB+Innel (интеграция)", "111", program="integration")

    result = json.loads(plugin.T.handle_telegram_search(
        {"query": "Innel", "count_only": True}, session_id="s",
    ))

    assert result["matched_chats"] == [{"chat_id": "-100700", "title": "WB+Innel (интеграция)"}]
    assert result["total_count"] == 0


def test_telegram_search_scoped_to_a_chat_id_only_matches_titles_of_that_chat(plugin):
    plugin.store.set_chat("-100700", "client", "WB+Innel (интеграция)", "111", program="integration")
    plugin.store.set_chat("-100800", "client", "WB+Innel Backup", "111", program="integration")

    result = json.loads(plugin.T.handle_telegram_search(
        {"query": "Innel", "chat_id": "-100700"}, session_id="s",
    ))

    assert result["matched_chats"] == [{"chat_id": "-100700", "title": "WB+Innel (интеграция)"}]


def test_telegram_search_no_match_at_all_reports_truly_empty(plugin):
    result = json.loads(plugin.T.handle_telegram_search({"query": "nonexistent"}, session_id="s"))

    assert result["matched_chats"] == []
    assert result["matches"] == []


# ── _ingest keeps the title index fresh for ANY observed chat ──────────────

def test_ingest_indexes_the_chat_title_even_for_an_unregistered_chat(plugin):
    msg = _group_message("hello", chat_id="-100900", chat_name="WB Random Group")

    plugin._ingest(msg)

    hits = plugin.store.search_chat_titles("Random Group")
    assert [h["chat_id"] for h in hits] == ["-100900"]


def test_ingest_refreshes_the_title_on_rename(plugin):
    plugin._ingest(_group_message("hi", chat_id="-100900", chat_name="Old Name"))
    plugin._ingest(_group_message("hi again", chat_id="-100900", chat_name="New Name"))

    assert plugin.store.search_chat_titles("Old Name") == []
    hits = plugin.store.search_chat_titles("New Name")
    assert hits[0]["chat_id"] == "-100900"
