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

    def _fake_send(chat_id, text, parse_mode=None):
        sent.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode})
        return True  # _send now reports confirmed delivery, not just "didn't raise"

    monkeypatch.setattr(mod, "_send", _fake_send)
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


def test_delivery_failure_is_reported_and_does_not_burn_the_cooldown(plugin, resolved_session, monkeypatch):
    """_send returning False (confirmed delivery failure — a MarkdownV2
    parse error, 429, bad token, bot removed from the team chat, ...) must
    not be reported as a success, and must NOT consume the cooldown — the
    whole point of checking the return value instead of assuming success
    from "the HTTP call didn't raise"."""
    _setup_client_chat(plugin)
    monkeypatch.setattr(plugin, "_send", lambda *a, **k: False)

    result = json.loads(plugin.T.handle_escalate_to_team(
        {"message": "see this"}, session_id=resolved_session,
    ))

    assert result["escalated"] is False
    assert result["reason"] == "delivery_failed"
    assert plugin.store.last_escalation_ts("-700") is None  # cooldown not burned


def test_delivery_failure_allows_an_immediate_retry(plugin, resolved_session, monkeypatch):
    _setup_client_chat(plugin)
    monkeypatch.setattr(plugin, "_send", lambda *a, **k: False)
    plugin.T.handle_escalate_to_team({"message": "first attempt"}, session_id=resolved_session)

    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text, parse_mode=None: (sent.append(text), True)[1])
    result = json.loads(plugin.T.handle_escalate_to_team(
        {"message": "retry"}, session_id=resolved_session,
    ))

    assert result["escalated"] is True
    assert len(sent) == 1


def test_non_numeric_message_id_is_dropped_rather_than_breaking_the_link(plugin, resolved_session):
    """message_id is model-supplied and interpolated into a MarkdownV2 URL —
    a non-digit value must be ignored (falling back to no specific-message
    link) rather than producing a malformed URL that breaks delivery."""
    _setup_client_chat(plugin)

    result = json.loads(plugin.T.handle_escalate_to_team(
        {"message": "see this", "message_id": "not-a-number\nwith a newline"},
        session_id=resolved_session,
    ))

    assert result["escalated"] is True
    assert "not-a-number" not in plugin._TEST_SENT[0]["text"]


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


# ── partner_flag_chats: the batch reviewer's own tool ───────────────────────
# Same _escalate_chat core as escalate_to_team, reached via explicit chat_ids
# instead of the caller's own session — used by the batch-review cron job,
# which has no single "origin chat" of its own.

def test_flag_chats_escalates_each_valid_entry(plugin):
    _setup_client_chat(plugin, client_chat="-700", team_chat="-500")
    _setup_client_chat(plugin, client_chat="-701", team_chat="-500")
    plugin.store.mark_reviewed("-700")
    plugin.store.mark_reviewed("-701")

    result = json.loads(plugin.T.handle_partner_flag_chats({"flags": [
        {"chat_id": "-700", "reason": "partner sent photos from the site"},
        {"chat_id": "-701", "reason": "partner asking about timeline"},
    ]}))

    assert result["count"] == 2
    assert all(r["escalated"] for r in result["results"])
    assert len(plugin._TEST_SENT) == 2
    assert {s["chat_id"] for s in plugin._TEST_SENT} == {"-500"}


def test_flag_chats_rejects_a_chat_id_that_isnt_a_registered_client_chat(plugin):
    """Defense in depth: even if the model hallucinated a chat_id not present
    in the script's own digest, the server-side chat_mode check refuses it —
    this must never be able to reach an arbitrary chat."""
    result = json.loads(plugin.T.handle_partner_flag_chats({"flags": [
        {"chat_id": "-999999", "reason": "made up"},
    ]}))

    assert result["results"][0]["ok"] is False
    assert plugin._TEST_SENT == []


def test_flag_chats_respects_the_same_cooldown_as_escalate_to_team(plugin, resolved_session):
    _setup_client_chat(plugin)
    plugin.store.mark_reviewed("-700")
    plugin.T.handle_escalate_to_team({"message": "first"}, session_id=resolved_session)

    result = json.loads(plugin.T.handle_partner_flag_chats({"flags": [
        {"chat_id": "-700", "reason": "second, too soon"},
    ]}))

    assert result["results"][0]["escalated"] is False
    assert result["results"][0]["reason"] == "cooldown"
    assert len(plugin._TEST_SENT) == 1  # the flag call never actually sent


def test_flag_chats_skips_entries_missing_chat_id_or_reason(plugin):
    result = json.loads(plugin.T.handle_partner_flag_chats({"flags": [
        {"chat_id": "-700"},
        {"reason": "no chat id given"},
    ]}))

    assert all(r["ok"] is False for r in result["results"])
    assert plugin._TEST_SENT == []


def test_flag_chats_rejects_non_list_flags(plugin):
    result = json.loads(plugin.T.handle_partner_flag_chats({"flags": "not-a-list"}))
    assert "error" in result


def test_flag_chats_rejects_a_valid_chat_not_in_the_current_batch(plugin):
    """The core of the H6 fix: a REAL, registered client chat that simply
    wasn't part of this tick's digest (never reviewed, or reviewed too long
    ago) must be refused — otherwise injected partner text in one chat's
    digest could steer the model into flagging an unrelated chat (possibly
    under a different program entirely), delivering attacker-chosen text
    into that other team's chat."""
    _setup_client_chat(plugin, client_chat="-700", team_chat="-500")
    # Deliberately NOT calling store.mark_reviewed("-700") — this chat is
    # registered and otherwise valid, but was never actually shown to the
    # batch-reviewer model.

    result = json.loads(plugin.T.handle_partner_flag_chats({"flags": [
        {"chat_id": "-700", "reason": "steered via injected text"},
    ]}))

    assert result["results"][0]["ok"] is False
    assert "current review batch" in result["results"][0]["error"]
    assert plugin._TEST_SENT == []


def test_flag_chats_rejects_a_stale_review_timestamp(plugin):
    """A chat reviewed long ago (well outside the recency window) must be
    treated the same as never-reviewed — the window exists so a stale
    leftover timestamp from a much earlier tick can't be replayed."""
    _setup_client_chat(plugin, client_chat="-700", team_chat="-500")
    plugin.store.mark_reviewed("-700", ts=time.time() - plugin.T._RECENTLY_REVIEWED_WINDOW_SECONDS - 60)

    result = json.loads(plugin.T.handle_partner_flag_chats({"flags": [
        {"chat_id": "-700", "reason": "too late"},
    ]}))

    assert result["results"][0]["ok"] is False


# ── program_client_chats: top-down enumeration + search, team-chat only ─────

def _add_msg(plugin, chat_id, mid, text, ts):
    plugin.store.add({
        "chat_id": chat_id, "message_id": str(mid), "ts": ts,
        "user_id": "p", "user_name": "Partner", "chat_type": "group",
        "chat_name": "", "thread_id": "", "text": text,
        "reply_to_message_id": "", "reply_to_author": "",
    })


def _team_session(plugin, monkeypatch, team_chat="-500"):
    monkeypatch.setattr(plugin.store, "origin_chat_id",
                        lambda session_id: team_chat if session_id == "team-sess" else None)
    return "team-sess"


def test_program_client_chats_lists_all_clients_from_the_team_chat(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme Corp", "222", program="journalist-partners")
    plugin.store.set_chat("-701", "client", "Beta LLC", "222", program="journalist-partners")
    _add_msg(plugin, "-700", 1, "older", 100.0)
    _add_msg(plugin, "-701", 1, "newer", 200.0)
    sess = _team_session(plugin, monkeypatch)

    result = json.loads(plugin.T.handle_program_client_chats({}, session_id=sess))

    assert result["program"] == "journalist-partners"
    assert result["count"] == 2
    # newest-active first
    assert [c["chat_id"] for c in result["client_chats"]] == ["-701", "-700"]
    assert {c["title"] for c in result["client_chats"]} == {"Acme Corp", "Beta LLC"}


def test_program_client_chats_blocked_outside_a_team_chat(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme Corp", "222", program="journalist-partners")
    # A client chat's session, not the team chat.
    monkeypatch.setattr(plugin.store, "origin_chat_id",
                        lambda session_id: "-700" if session_id == "sess" else None)

    result = json.loads(plugin.T.handle_program_client_chats({}, session_id="sess"))

    assert "error" in result


def test_program_client_chats_query_matches_title(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme Corp", "222", program="journalist-partners")
    plugin.store.set_chat("-701", "client", "Beta LLC", "222", program="journalist-partners")
    sess = _team_session(plugin, monkeypatch)

    result = json.loads(plugin.T.handle_program_client_chats({"query": "acme"}, session_id=sess))

    assert result["count"] == 1
    assert result["client_chats"][0]["chat_id"] == "-700"
    assert result["client_chats"][0]["matched_on"] == "title"


def test_program_client_chats_query_matches_message_content(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme Corp", "222", program="journalist-partners")
    plugin.store.set_chat("-701", "client", "Beta LLC", "222", program="journalist-partners")
    _add_msg(plugin, "-701", 1, "вопрос про CAN шину и терминаторы", 200.0)
    sess = _team_session(plugin, monkeypatch)

    result = json.loads(plugin.T.handle_program_client_chats({"query": "CAN"}, session_id=sess))

    assert result["count"] == 1
    hit = result["client_chats"][0]
    assert hit["chat_id"] == "-701"
    assert hit["matched_on"] == "content"
    assert "CAN" in hit["snippet"]


def test_program_client_chats_query_does_not_leak_other_programs_chats(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.create_program("support-clients", "-600", "111")
    plugin.store.set_chat("-700", "client", "Acme Corp", "222", program="journalist-partners")
    plugin.store.set_chat("-800", "client", "Other Co", "333", program="support-clients")
    _add_msg(plugin, "-800", 1, "CAN bus question in another program", 200.0)
    sess = _team_session(plugin, monkeypatch)  # team chat -500 = journalist-partners

    result = json.loads(plugin.T.handle_program_client_chats({"query": "CAN"}, session_id=sess))

    # -800 belongs to support-clients, not this team's program — must not appear.
    assert all(c["chat_id"] != "-800" for c in result["client_chats"])


def test_program_client_chats_empty_list_explains_it_is_not_an_access_limit(plugin, monkeypatch):
    """Regression for a live 'тупняк': with no client chats registered, the tool
    must make clear the team chat still has full access — an empty list means
    'none registered', not 'I can't see them'."""
    plugin.store.create_program("journalist-partners", "-500", "111")  # team chat, no clients
    sess = _team_session(plugin, monkeypatch)

    result = json.loads(plugin.T.handle_program_client_chats({}, session_id=sess))

    assert result["count"] == 0
    assert "note" in result
    assert "not an access limitation" in result["note"].lower()
