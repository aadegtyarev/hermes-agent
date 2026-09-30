"""Tests for the escalate_to_team tool in telegram-context.

The "call in a senior colleague" primitive for isolated client chats: posts
to the program's own team chat (never a DM), rate-limited per chat, and only
usable from inside a chat registered as client-mode under some program.

Unlike the other telegram-context test files, this fixture loads the plugin
package EXACTLY the way hermes_cli/plugins.py's real loader does — one
importlib spec with submodule_search_locations, registered at
sys.modules[pkg_name] itself (not a separate '.__init__' key) — because
handle_escalate_to_team (in tools.py) does a LAZY `from . import _send, ...`
to reach names defined in __init__.py, and that relative import only
resolves correctly when the package module object is the real, fully-loaded
__init__.py content, matching production. The other test files in this
directory stub out tools.py entirely (they don't need it); this one needs
the real module loaded, so it needs the accurate loading pattern.
"""
from __future__ import annotations

import importlib.util
import itertools
import json
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
def plugin(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    pkg_name = f"telegram_context_escalation_under_test_{next(_counter)}"
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

    sent = []
    monkeypatch.setattr(mod, "_send", lambda chat_id, text, parse_mode=None:
                         sent.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode}))
    mod._TEST_SENT = sent

    yield mod

    for key in list(sys.modules):
        if key == pkg_name or key.startswith(pkg_name + "."):
            del sys.modules[key]


def _setup_client_chat(plugin, client_chat="-700", team_chat="-500", program="journalist-partners"):
    plugin.store.create_program(program, team_chat, "111")
    plugin.store.set_chat(client_chat, "client", "Acme Corp", "222", program=program)


# ── The lazy cross-module import itself actually resolves ──────────────────

def test_escalate_handler_is_reachable_and_does_not_import_error(plugin):
    """Regression guard for the specific bug this fixture was built to catch:
    tools.py's `from . import _send` must resolve against the real loaded
    package, not raise ImportError/AttributeError."""
    _setup_client_chat(plugin)
    result = json.loads(plugin.T.handle_escalate_to_team(
        {"message": "partner sent three photos from the site"}, session_id="ignored-for-this-test",
    ))
    # No resolvable session in this call -> the tool's own "not a client chat"
    # guard fires (session_id doesn't map to a real session), which is a
    # clean tool_error, not a crash. That's the point of this test: it proves
    # the import machinery works at all, not the full success path (covered
    # below via _origin_chat_id mocking).
    assert "error" in result


# ── Full success/guard paths, with store.origin_chat_id mocked ─────────────

@pytest.fixture
def resolved_session(plugin, monkeypatch):
    """Make store.origin_chat_id("sess-1") resolve to "-700" without a real
    SessionDB — same technique as the other isolation tests in this suite."""
    monkeypatch.setattr(plugin.store, "origin_chat_id", lambda session_id:
                         "-700" if session_id == "sess-1" else None)
    return "sess-1"


def test_escalation_blocked_outside_a_client_chat(plugin, resolved_session):
    result = json.loads(plugin.T.handle_escalate_to_team(
        {"message": "hello"}, session_id=resolved_session,
    ))
    assert "error" in result  # -700 isn't registered as anything yet


def test_successful_escalation_posts_to_the_team_chat(plugin, resolved_session):
    _setup_client_chat(plugin)

    result = json.loads(plugin.T.handle_escalate_to_team(
        {"message": "partner is asking about the invoice"}, session_id=resolved_session,
    ))

    assert result["escalated"] is True
    assert result["team_chat"] == "-500"
    assert len(plugin._TEST_SENT) == 1
    sent = plugin._TEST_SENT[0]
    assert sent["chat_id"] == "-500"
    assert sent["parse_mode"] == "MarkdownV2"
    assert "Acme Corp" in sent["text"]
    assert "invoice" in sent["text"]


def test_escalation_requires_a_message(plugin, resolved_session):
    _setup_client_chat(plugin)

    result = json.loads(plugin.T.handle_escalate_to_team({}, session_id=resolved_session))

    assert "error" in result
    assert plugin._TEST_SENT == []


def test_escalation_cooldown_blocks_a_repeat_call(plugin, resolved_session):
    _setup_client_chat(plugin)
    plugin.T.handle_escalate_to_team({"message": "first"}, session_id=resolved_session)

    result = json.loads(plugin.T.handle_escalate_to_team(
        {"message": "second, too soon"}, session_id=resolved_session,
    ))

    assert result["escalated"] is False
    assert result["reason"] == "cooldown"
    assert result["retry_after_seconds"] > 0
    assert len(plugin._TEST_SENT) == 1  # the repeat never actually sent


def test_escalation_allowed_again_after_cooldown_elapses(plugin, resolved_session):
    _setup_client_chat(plugin)
    plugin.store.record_escalation("-700", time.time() - plugin.T._ESCALATION_COOLDOWN_SECONDS - 1)

    result = json.loads(plugin.T.handle_escalate_to_team(
        {"message": "new situation"}, session_id=resolved_session,
    ))

    assert result["escalated"] is True
    assert len(plugin._TEST_SENT) == 1


def test_escalation_uses_message_id_in_deep_link_when_given(plugin, monkeypatch):
    _setup_client_chat(plugin, client_chat="-100700")  # -100-prefixed -> deep-linkable
    monkeypatch.setattr(plugin.store, "origin_chat_id", lambda session_id: "-100700")

    plugin.T.handle_escalate_to_team(
        {"message": "see this", "message_id": "42"}, session_id="sess-1",
    )

    text = plugin._TEST_SENT[0]["text"]
    assert "t.me/c/700/42" in text


def test_escalation_falls_back_to_no_link_for_a_non_supergroup_chat_id(plugin, resolved_session):
    _setup_client_chat(plugin)  # -700, not -100-prefixed

    plugin.T.handle_escalate_to_team({"message": "see this"}, session_id=resolved_session)

    text = plugin._TEST_SENT[0]["text"]
    assert "t.me" not in text
    assert "Acme Corp" in text


def test_special_characters_in_message_do_not_break_markdownv2(plugin, resolved_session):
    _setup_client_chat(plugin)

    result = json.loads(plugin.T.handle_escalate_to_team(
        {"message": "invoice #42 costs $100 (urgent!)"}, session_id=resolved_session,
    ))

    assert result["escalated"] is True  # no exception raised building the message
    assert "\\#42" in plugin._TEST_SENT[0]["text"]
    assert "\\(urgent\\!\\)" in plugin._TEST_SENT[0]["text"]


# ── _md2_escape / _md2_link / _telegram_chat_deep_link: direct unit tests ──

def test_md2_escape_escapes_all_special_characters(plugin):
    assert plugin._md2_escape("a.b!c(d)e-f") == "a\\.b\\!c\\(d\\)e\\-f"


def test_md2_link_builds_masked_link(plugin):
    assert plugin._md2_link("My Chat", "https://t.me/c/700/1") == "[My Chat](https://t.me/c/700/1)"


def test_md2_link_falls_back_to_escaped_text_without_a_url(plugin):
    assert plugin._md2_link("My Chat!", None) == "My Chat\\!"


def test_deep_link_builds_for_supergroup_negative_id(plugin):
    assert plugin._telegram_chat_deep_link("-100700", "42") == "https://t.me/c/700/42"


def test_deep_link_defaults_message_id_to_one(plugin):
    assert plugin._telegram_chat_deep_link("-100700") == "https://t.me/c/700/1"


def test_deep_link_none_for_a_basic_group_id(plugin):
    assert plugin._telegram_chat_deep_link("-700") is None
