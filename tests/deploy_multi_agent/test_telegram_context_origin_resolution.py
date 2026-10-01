"""Tests for store.py's origin_chat_id() and the _conn() fd-leak fix.

Both found by an adversarial review of the client-chat isolation feature:

- origin_chat_id didn't follow parent_session_id, so mid-turn context
  compression (which rotates agent.session_id to a fresh row recording only
  parent_session_id — the gateway backfills that row's own chat_id only
  AFTER the turn returns) made every isolation guard resolve to "unknown
  chat" for the rest of a compressed turn, silently lifting every
  restriction exactly in the long-running conversations this feature
  targets.
- _conn() (and the SessionDB instance origin_chat_id creates) never closed
  the connection — sqlite3.Connection's own context manager only
  commits/rolls back, it does not close. Every store.py call — not just
  this feature's — leaked one fd. Eventually hits the process fd limit,
  and the isolation guards fail OPEN when a store call raises.
"""
from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path

import pytest

_PLUGIN_DIR = (
    Path(__file__).resolve().parents[2]
    / "deploy" / "multi-agent" / "base" / "plugins" / "telegram-context"
)
_counter = itertools.count()


@pytest.fixture
def store_mod(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    mod_name = f"telegram_context_origin_resolution_under_test_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(mod_name, _PLUGIN_DIR / "store.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    mod.init()
    yield mod
    del sys.modules[mod_name]


class _FakeSessionDB:
    """session_id -> row, with .close() tracked so the fd-leak fix is verifiable."""
    _ROWS: dict[str, dict] = {}
    close_calls = 0

    def __init__(self, read_only: bool = False):
        pass

    def get_session(self, session_id):
        return self._ROWS.get(session_id)

    def close(self):
        type(self).close_calls += 1


@pytest.fixture
def fake_session_db(monkeypatch):
    import hermes_state

    _FakeSessionDB._ROWS = {}
    _FakeSessionDB.close_calls = 0
    monkeypatch.setattr(hermes_state, "SessionDB", _FakeSessionDB)
    return _FakeSessionDB


# ── origin_chat_id: direct resolution, source check, missing/unknown ───────

def test_resolves_chat_id_directly_when_present(store_mod, fake_session_db):
    fake_session_db._ROWS["sess-1"] = {"chat_id": "-700", "source": "telegram", "parent_session_id": None}
    assert store_mod.origin_chat_id("sess-1") == "-700"


def test_rejects_a_non_telegram_source(store_mod, fake_session_db):
    """A chat_id collision with a differently-sourced session must not be
    treated as a Telegram chat."""
    fake_session_db._ROWS["sess-1"] = {"chat_id": "-700", "source": "cli", "parent_session_id": None}
    assert store_mod.origin_chat_id("sess-1") is None


def test_returns_none_for_unknown_session(store_mod, fake_session_db):
    assert store_mod.origin_chat_id("never-heard-of-it") is None


def test_returns_none_for_empty_session_id(store_mod, fake_session_db):
    assert store_mod.origin_chat_id("") is None
    assert store_mod.origin_chat_id(None) is None


# ── Following parent_session_id (the compression-rotation case) ───────────

def test_follows_parent_session_id_when_own_chat_id_is_empty(store_mod, fake_session_db):
    """The exact scenario found live: mid-turn compression creates a new
    session row with no chat_id of its own yet, only a parent link."""
    fake_session_db._ROWS["parent-sess"] = {"chat_id": "-700", "source": "telegram", "parent_session_id": None}
    fake_session_db._ROWS["compressed-sess"] = {"chat_id": "", "source": "telegram", "parent_session_id": "parent-sess"}

    assert store_mod.origin_chat_id("compressed-sess") == "-700"


def test_follows_a_multi_hop_parent_chain(store_mod, fake_session_db):
    fake_session_db._ROWS["grandparent"] = {"chat_id": "-700", "source": "telegram", "parent_session_id": None}
    fake_session_db._ROWS["parent"] = {"chat_id": "", "source": "telegram", "parent_session_id": "grandparent"}
    fake_session_db._ROWS["child"] = {"chat_id": "", "source": "telegram", "parent_session_id": "parent"}

    assert store_mod.origin_chat_id("child") == "-700"


def test_depth_capped_against_a_cyclic_parent_chain(store_mod, fake_session_db):
    """A corrupted/cyclic parent_session_id chain must not infinite-loop or
    stack-overflow — it must fail closed to 'unknown', not hang the hook."""
    fake_session_db._ROWS["a"] = {"chat_id": "", "source": "telegram", "parent_session_id": "b"}
    fake_session_db._ROWS["b"] = {"chat_id": "", "source": "telegram", "parent_session_id": "a"}

    assert store_mod.origin_chat_id("a") is None


def test_no_chat_id_and_no_parent_resolves_to_none(store_mod, fake_session_db):
    fake_session_db._ROWS["orphan"] = {"chat_id": "", "source": "telegram", "parent_session_id": None}
    assert store_mod.origin_chat_id("orphan") is None


# ── fd hygiene: the SessionDB handle is always closed ───────────────────────

def test_session_db_is_closed_on_success(store_mod, fake_session_db):
    fake_session_db._ROWS["sess-1"] = {"chat_id": "-700", "source": "telegram", "parent_session_id": None}
    store_mod.origin_chat_id("sess-1")
    assert fake_session_db.close_calls == 1


def test_session_db_is_closed_even_on_lookup_error(store_mod, fake_session_db, monkeypatch):
    def _boom(self, session_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(fake_session_db, "get_session", _boom)
    assert store_mod.origin_chat_id("sess-1") is None
    assert fake_session_db.close_calls == 1


def test_session_db_closed_once_per_hop_in_a_parent_chain(store_mod, fake_session_db):
    fake_session_db._ROWS["parent"] = {"chat_id": "-700", "source": "telegram", "parent_session_id": None}
    fake_session_db._ROWS["child"] = {"chat_id": "", "source": "telegram", "parent_session_id": "parent"}

    store_mod.origin_chat_id("child")

    assert fake_session_db.close_calls == 2  # one per recursive hop


# ── _conn() actually closes the connection (the broader, pre-existing leak) ─

def test_conn_closes_the_connection_on_success(store_mod):
    with store_mod._conn() as c:
        c.execute("SELECT 1")
    with pytest.raises(Exception):
        c.execute("SELECT 1")  # ProgrammingError: Cannot operate on a closed database


def test_conn_closes_the_connection_even_on_exception(store_mod):
    captured = {}
    try:
        with store_mod._conn() as c:
            captured["c"] = c
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with pytest.raises(Exception):
        captured["c"].execute("SELECT 1")


def test_conn_still_commits_writes_on_success(store_mod):
    """The wrapper must preserve the original commit-on-success behavior —
    not just add closing. Exercised indirectly: add() + recent() across two
    separate _conn() calls only works if the first one's write was durably
    committed, not left in an uncommitted transaction."""
    store_mod.add({
        "chat_id": "-700", "message_id": "1", "ts": 0, "user_id": "1", "user_name": "A",
        "chat_type": "group", "chat_name": "T", "thread_id": "", "text": "hello",
        "reply_to_message_id": "", "reply_to_author": "",
    })
    assert store_mod.recent("-700", 10)["messages"][0]["text"] == "hello"
