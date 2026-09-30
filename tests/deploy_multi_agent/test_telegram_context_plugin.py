"""Tests for deploy/multi-agent/base/plugins/telegram-context.

Focus: the pre_tool_call hard read-only guard (_pre_tool_call) and the
regression it was written to fix — that /hermes_readonly must never lock an
admin out of managing the very chat it was run in. See the module docstring
in telegram-context/__init__.py for the full story (an earlier version tried
to get the "never writes there" guarantee by mirroring this plugin's readonly
set into hermes-agent core's own read_only_chats field, which also gates
dispatch and silently stopped /hermes_forget from reaching this plugin's own
hook for a chat it had just marked read-only).

This deploy bundle isn't part of the importable hermes-agent package tree
(hyphenated directory name, loaded by render.py's own file-copy bundling at
deploy time, not a Python package) — loaded here via importlib the same way,
so these tests exercise the real module rather than a reimplementation.
"""
from __future__ import annotations

import importlib.util
import itertools
import sys
import types
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
    """Load a fresh copy of the plugin module, with its own isolated
    HERMES_HOME (so store.py's SQLite file never touches a real one) and a
    stubbed `tools` submodule (tools.py pulls in tools.registry, core
    plumbing unrelated to what's under test here).

    A unique module name per call keeps tests independent even though
    they share a subprocess (run_tests_parallel.py isolates per FILE, not
    per test function).
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_READONLY_CHATS", raising=False)
    monkeypatch.delenv("TELEGRAM_HOME_CHANNEL", raising=False)

    pkg_name = f"telegram_context_under_test_{next(_counter)}"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(_PLUGIN_DIR)]
    sys.modules[pkg_name] = pkg

    store_spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.store", _PLUGIN_DIR / "store.py"
    )
    store_mod = importlib.util.module_from_spec(store_spec)
    sys.modules[f"{pkg_name}.store"] = store_mod
    store_spec.loader.exec_module(store_mod)

    tools_mod = types.ModuleType(f"{pkg_name}.tools")
    tools_mod.TOOLS = ()
    sys.modules[f"{pkg_name}.tools"] = tools_mod

    init_spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.__init__", _PLUGIN_DIR / "__init__.py"
    )
    mod = importlib.util.module_from_spec(init_spec)
    mod.__package__ = pkg_name
    sys.modules[f"{pkg_name}.__init__"] = mod
    init_spec.loader.exec_module(mod)
    mod.store.init()

    yield mod

    for key in list(sys.modules):
        if key.startswith(pkg_name):
            del sys.modules[key]


def _group_message(text, *, chat_id="-100", from_user_id="111", chat_name="Test Group"):
    return SimpleNamespace(
        message_id="42",
        text=text,
        source=SimpleNamespace(
            platform="telegram", chat_id=chat_id, user_id=from_user_id,
            user_name="Alice", chat_type="group", chat_name=chat_name,
            thread_id=None,
        ),
        reply_to_message_id=None,
        reply_to_author_name=None,
    )


# ── _pre_tool_call: the actual hard guarantee ───────────────────────────────

def test_blocks_send_message_targeting_a_readonly_chat_env(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_READONLY_CHATS", "-100")

    result = plugin._pre_tool_call(
        tool_name="send_message", args={"target": "telegram:-100", "message": "hi"},
    )

    assert result is not None
    assert result["action"] == "block"
    assert "-100" in result["message"]


def test_blocks_send_message_targeting_a_readonly_chat_from_runtime_store(plugin):
    plugin.store.set_chat("-200", "readonly", "Public chat", "662750197")

    result = plugin._pre_tool_call(
        tool_name="send_message", args={"target": "telegram:-200", "message": "hi"},
    )

    assert result is not None and result["action"] == "block"


def test_allows_send_message_to_a_non_readonly_chat(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_READONLY_CHATS", "-100")

    result = plugin._pre_tool_call(
        tool_name="send_message", args={"target": "telegram:-999", "message": "hi"},
    )

    assert result is None


def test_thread_suffix_does_not_bypass_the_block(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_READONLY_CHATS", "-100")

    result = plugin._pre_tool_call(
        tool_name="send_message",
        args={"target": "telegram:-100:17585", "message": "hi"},
    )

    assert result is not None and result["action"] == "block"


def test_bare_telegram_target_resolves_against_home_channel(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_READONLY_CHATS", "-100")
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "-100")

    result = plugin._pre_tool_call(
        tool_name="send_message", args={"target": "telegram", "message": "hi"},
    )

    assert result is not None and result["action"] == "block"


def test_react_action_is_blocked_too_not_just_plain_send(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_READONLY_CHATS", "-100")

    result = plugin._pre_tool_call(
        tool_name="send_message",
        args={"action": "react", "target": "telegram:-100", "emoji": "\U0001f44d"},
    )

    assert result is not None and result["action"] == "block"


def test_other_platforms_never_matched_even_with_colliding_numeric_id(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_READONLY_CHATS", "999888777")

    result = plugin._pre_tool_call(
        tool_name="send_message",
        args={"target": "discord:999888777:555444333", "message": "hi"},
    )

    assert result is None


def test_ignores_tool_calls_other_than_send_message(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_READONLY_CHATS", "-100")

    result = plugin._pre_tool_call(
        tool_name="terminal", args={"command": "echo telegram:-100"},
    )

    assert result is None


def test_missing_or_empty_target_is_a_safe_no_op(plugin):
    assert plugin._pre_tool_call(tool_name="send_message", args={}) is None
    assert plugin._pre_tool_call(tool_name="send_message", args=None) is None


# ── Regression: /hermes_forget must still work from inside the RO chat ─────
# (the actual bug in the reverted core-mirroring approach — a chat marked
# read-only silently stopped reaching this plugin's own dispatch hook at all)

def test_hermes_readonly_then_hermes_forget_round_trip_from_inside_the_chat(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    chat_id = "-300"

    msg1 = _group_message("/hermes_readonly", chat_id=chat_id)
    result1 = plugin._handle_command(msg1, msg1.source, chat_id, "111")
    assert result1 == {"action": "skip", "reason": "telegram chat command handled"}
    assert chat_id in plugin._readonly_chats()

    # The whole point: a second admin command typed INSIDE the now-read-only
    # chat must still reach _handle_command — nothing in this plugin's own
    # logic gates dispatch on chats_allowed before admin commands are checked.
    msg2 = _group_message("/hermes_forget", chat_id=chat_id)
    result2 = plugin._handle_command(msg2, msg2.source, chat_id, "111")
    assert result2 == {"action": "skip", "reason": "telegram chat command handled"}
    assert chat_id not in plugin._readonly_chats()


def test_non_admin_cannot_run_hermes_commands(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    msg = _group_message("/hermes_readonly", chat_id="-400")

    result = plugin._handle_command(msg, msg.source, "-400", "999")

    assert result == {"action": "skip", "reason": "telegram chat command from non-admin"}
    assert "-400" not in plugin._readonly_chats()


def test_non_admin_gets_total_silence_not_a_permission_denied_reply(plugin, monkeypatch):
    """A non-admin poking /hermes_* must get no reply at all — not even an
    error — so the command's existence isn't confirmed to them."""
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))
    msg = _group_message("/hermes_here", chat_id="-400")

    plugin._handle_command(msg, msg.source, "-400", "999")

    assert sent == []


# ── Regression: the removed core-mirroring function is actually gone ───────

def test_sync_core_read_only_was_removed(plugin):
    """Guards against silently reintroducing the reverted approach (see the
    module docstring): mirroring into hermes-agent core's read_only_chats
    also gates core dispatch, which is what caused the /hermes_forget
    lockout this test file exists to prevent."""
    assert not hasattr(plugin, "_sync_core_read_only")


def test_register_installs_the_pre_tool_call_hook(plugin):
    calls = []

    class FakeCtx:
        def register_tool(self, **kw):
            pass

        def register_hook(self, name, handler):
            calls.append((name, handler))

        def register_command(self, **kw):
            pass

    plugin.register(FakeCtx())

    hook_names = [name for name, _ in calls]
    assert "pre_tool_call" in hook_names
    assert "pre_gateway_dispatch" in hook_names
    registered = dict(calls)
    assert registered["pre_tool_call"] is plugin._pre_tool_call


# ── Nightly backup cron job registration ────────────────────────────────────

def test_ensure_backup_cron_job_registers_once(plugin, tmp_path):
    pytest.importorskip("croniter")  # real hermes-agent dependency; may be
    # missing only in an ad-hoc local venv, never in the production image
    from cron import jobs as cron_jobs

    plugin._ensure_backup_cron_job()

    registered = [j for j in cron_jobs.list_jobs(include_disabled=True)
                  if j.get("name") == plugin._BACKUP_JOB_NAME]
    assert len(registered) == 1
    job = registered[0]
    assert job.get("script") == plugin._BACKUP_SCRIPT_FILENAME
    assert job.get("no_agent") is True

    script_copy = tmp_path / "scripts" / plugin._BACKUP_SCRIPT_FILENAME
    assert script_copy.exists()
    assert "sqlite3" in script_copy.read_text(encoding="utf-8")

    # Idempotent: calling again must not create a second job.
    plugin._ensure_backup_cron_job()
    still_one = [j for j in cron_jobs.list_jobs(include_disabled=True)
                 if j.get("name") == plugin._BACKUP_JOB_NAME]
    assert len(still_one) == 1
