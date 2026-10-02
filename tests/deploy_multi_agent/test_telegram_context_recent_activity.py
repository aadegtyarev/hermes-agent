"""Tests for deploy/multi-agent/base/plugins/telegram-context/store.py's
chat_has_recent_activity() — the "active dialogue" signal that gates
synchronous image auto-description in __init__.py's _ingest() (see
test_telegram_context_image_autodescribe.py for that side).
"""
from __future__ import annotations

import importlib.util
import itertools
import sys
import time
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


def _add(store, chat_id, ts, message_id):
    store.add({
        "chat_id": chat_id, "message_id": message_id, "ts": ts,
        "user_id": "u1", "user_name": "Alice", "chat_type": "group",
        "chat_name": "Test", "thread_id": "", "text": "hi",
        "reply_to_message_id": "", "reply_to_author": "",
    })


def test_no_messages_is_not_active(store):
    assert store.chat_has_recent_activity("C1", window_seconds=600) is False


def test_empty_chat_id_is_not_active(store):
    _add(store, "C1", time.time(), "0")
    assert store.chat_has_recent_activity("", window_seconds=600) is False


def test_recent_message_counts_as_active(store):
    _add(store, "C1", time.time() - 10, "0")
    assert store.chat_has_recent_activity("C1", window_seconds=600) is True


def test_old_message_outside_window_is_not_active(store):
    _add(store, "C1", time.time() - 3600, "0")
    assert store.chat_has_recent_activity("C1", window_seconds=600) is False


def test_activity_is_scoped_per_chat(store):
    _add(store, "C1", time.time() - 10, "0")
    assert store.chat_has_recent_activity("C2", window_seconds=600) is False


def test_before_ts_anchors_the_window_instead_of_now(store):
    # A message 30 minutes before some reference point, checked with a
    # 10-minute window anchored at a reference point only 5 minutes after it.
    anchor = time.time()
    _add(store, "C1", anchor - 1800, "0")
    assert store.chat_has_recent_activity("C1", window_seconds=600, before_ts=anchor - 1500) is True
    assert store.chat_has_recent_activity("C1", window_seconds=600, before_ts=anchor) is False
