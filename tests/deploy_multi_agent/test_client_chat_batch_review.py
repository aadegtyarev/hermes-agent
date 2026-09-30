"""Tests for deploy/multi-agent/base/plugins/telegram-context/client_chat_batch_review.py.

The cheap, no-LLM filtering pass that decides which client chats are
actually due for a batch-review look — cron/scheduler.py treats empty
stdout from a script= (agent-invoking) job as "nothing to report, skip the
LLM call entirely" (see cron/scheduler.py's _build_job_prompt), which is the
real anti-spam mechanism this whole feature rests on: most ticks should
produce nothing and cost a few sqlite reads, not an LLM call.

Loaded directly via importlib (no relative imports in the script itself,
same pattern as test_telegram_db_backup.py) rather than through the plugin
package, since this runs as its own subprocess in production too.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "deploy" / "multi-agent" / "base" / "plugins" / "telegram-context"
    / "client_chat_batch_review.py"
)
_STORE_PATH = _SCRIPT_PATH.parent / "store.py"


@pytest.fixture
def review_mod(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("client_chat_batch_review_under_test", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def store_mod(monkeypatch, tmp_path):
    """The real store.py, for realistic seeding (chats_allowed/partner_programs/
    messages schema) — points at the SAME tmp_path HERMES_HOME as review_mod
    (set independently here too, so fixture resolution order never matters)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("client_chat_batch_review_store_under_test", _STORE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["client_chat_batch_review_store_under_test"] = mod
    spec.loader.exec_module(mod)
    mod.init()
    yield mod
    del sys.modules["client_chat_batch_review_store_under_test"]


def _seed_chat(store_mod, chat_id="-700", program="journalist-partners", team_chat="-500"):
    store_mod.create_program(program, team_chat, "111")
    store_mod.set_chat(chat_id, "client", "Acme", "222", program=program)


def _add_message(store_mod, chat_id, user_id, user_name, text, ts=None):
    store_mod.add({
        "chat_id": chat_id, "message_id": str(int((ts or time.time()) * 1000)),
        "ts": ts if ts is not None else time.time(),
        "user_id": user_id, "user_name": user_name, "chat_type": "group",
        "chat_name": "Acme", "thread_id": "", "text": text,
        "reply_to_message_id": "", "reply_to_author": "",
    })


# ── _tier_interval_seconds ───────────────────────────────────────────────────

def test_quiet_chat_gets_the_shortest_interval(review_mod):
    assert review_mod._tier_interval_seconds(0) == 5 * 60
    assert review_mod._tier_interval_seconds(1) == 5 * 60


def test_busy_chat_gets_the_longest_interval(review_mod):
    assert review_mod._tier_interval_seconds(50) == 60 * 60


def test_normal_activity_gets_the_middle_interval(review_mod):
    assert review_mod._tier_interval_seconds(5) == 20 * 60


# ── main()/_due_chats(): end-to-end against a real seeded telegram.db ──────

def test_no_client_chats_produces_no_output(review_mod, store_mod, capsys):
    assert review_mod.main() == 0
    assert capsys.readouterr().out == ""


def test_freshly_registered_chat_with_unanswered_message_is_due(review_mod, store_mod, monkeypatch, capsys):
    _seed_chat(store_mod)
    _add_message(store_mod, "-700", "999", "Partner Ivan", "when can we publish the article?")
    monkeypatch.setattr(review_mod, "_is_live_member", lambda chat_id, uid: False)

    assert review_mod.main() == 0
    out = capsys.readouterr().out
    assert "Acme" in out
    assert "when can we publish the article?" in out


def test_chat_last_answered_by_a_team_member_is_not_due(review_mod, store_mod, monkeypatch, capsys):
    _seed_chat(store_mod)
    _add_message(store_mod, "-700", "111", "Journalist", "already handled, thanks!")
    monkeypatch.setattr(review_mod, "_is_live_member", lambda chat_id, uid: uid == "111")

    assert review_mod.main() == 0
    assert capsys.readouterr().out == ""


def test_chat_not_yet_due_by_activity_interval_is_skipped(review_mod, store_mod, monkeypatch, capsys):
    _seed_chat(store_mod)
    _add_message(store_mod, "-700", "999", "Partner Ivan", "hello?")
    monkeypatch.setattr(review_mod, "_is_live_member", lambda chat_id, uid: False)
    # Mark it as already reviewed moments ago — not due again for a while.
    with review_mod._conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO chat_review_state(chat_id, last_reviewed_ts) VALUES(?,?)",
            ("-700", time.time() - 10),
        )

    assert review_mod.main() == 0
    assert capsys.readouterr().out == ""


def test_due_chat_updates_its_own_review_state(review_mod, store_mod, monkeypatch):
    _seed_chat(store_mod)
    _add_message(store_mod, "-700", "999", "Partner Ivan", "hello?")
    monkeypatch.setattr(review_mod, "_is_live_member", lambda chat_id, uid: False)

    before = time.time()
    review_mod.main()

    with review_mod._conn() as c:
        row = c.execute(
            "SELECT last_reviewed_ts FROM chat_review_state WHERE chat_id=?", ("-700",)
        ).fetchone()
    assert row is not None
    assert row["last_reviewed_ts"] >= before


def test_chat_with_no_program_is_skipped(review_mod, store_mod, capsys):
    store_mod.set_chat("-700", "client", "Acme", "222", program=None)

    assert review_mod.main() == 0
    assert capsys.readouterr().out == ""


def test_only_messages_since_last_review_are_included(review_mod, store_mod, monkeypatch, capsys):
    _seed_chat(store_mod)
    old_ts = time.time() - 7200
    _add_message(store_mod, "-700", "999", "Partner Ivan", "old message, already reviewed", ts=old_ts)
    with review_mod._conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO chat_review_state(chat_id, last_reviewed_ts) VALUES(?,?)",
            ("-700", old_ts + 60),
        )
    _add_message(store_mod, "-700", "999", "Partner Ivan", "new message after review")
    monkeypatch.setattr(review_mod, "_is_live_member", lambda chat_id, uid: False)

    review_mod.main()

    out = capsys.readouterr().out
    assert "new message after review" in out
    assert "old message, already reviewed" not in out


def test_is_live_member_returns_false_without_a_bot_token(review_mod, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert review_mod._is_live_member("-500", "111") is False


def test_is_live_member_handles_network_errors_safely(review_mod, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    def _boom(*a, **k):
        raise OSError("network unreachable")

    monkeypatch.setattr(review_mod.urllib.request, "urlopen", _boom)
    assert review_mod._is_live_member("-500", "111") is False
