"""Tests for the client-chat invite-link mechanism in telegram-context.

A "join" link for a client chat, usable by someone NOT already a member —
unlike the existing _telegram_chat_deep_link (which only resolves for an
existing member). Two sources: a bot-generated invite link (cached forever
once created — see store.chat_invite_links' own docstring for why only this
source is cached), or a link found by scanning the chat's own description
(never cached — a human can edit that at any time). Plus /hermes_invite_link,
a team-member-trust command to force a fresh one when the cached one goes
stale (revoked in Telegram's own UI, chat recreated, etc.).
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


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    pkg_name = f"telegram_context_invite_links_under_test_{next(_counter)}"
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

    # membership: {chat_id: {uid: status}} — drives the fake getChatMember,
    # the only Bot API call the /hermes_invite_link command tests below
    # actually need live (they mock _create_chat_invite_link/
    # _get_chat_description directly, so this fake never needs to speak
    # those endpoints' shapes).
    mod._TEST_MEMBERSHIP = {}

    def _fake_urlopen(url, timeout=8):
        import urllib.parse as up

        parsed = up.urlparse(url)
        qs = up.parse_qs(parsed.query)
        chat_id = qs.get("chat_id", [""])[0]
        uid = qs.get("user_id", [""])[0]
        status = mod._TEST_MEMBERSHIP.get(chat_id, {}).get(uid)
        if status is None:
            return _FakeHTTPResponse({"ok": True, "result": {"status": "left"}})
        return _FakeHTTPResponse({"ok": True, "result": {"status": status}})

    monkeypatch.setattr(mod.urllib.request, "urlopen", _fake_urlopen)

    yield mod

    for key in list(sys.modules):
        if key == pkg_name or key.startswith(pkg_name + "."):
            del sys.modules[key]


def _member(plugin, chat_id: str, uid: str) -> None:
    plugin._TEST_MEMBERSHIP.setdefault(chat_id, {})[uid] = "member"


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


# ── store.py: cached invite link ────────────────────────────────────────────

def test_cached_invite_link_roundtrip(plugin):
    assert plugin.store.get_cached_invite_link("-700") is None
    plugin.store.set_cached_invite_link("-700", "https://t.me/+abc")
    assert plugin.store.get_cached_invite_link("-700") == "https://t.me/+abc"


def test_forget_chat_state_clears_cached_invite_link(plugin):
    plugin.store.set_cached_invite_link("-700", "https://t.me/+abc")
    plugin.store.forget_chat_state("-700")
    assert plugin.store.get_cached_invite_link("-700") is None


def test_remove_program_cascade_clears_cached_invite_links_of_its_clients(plugin):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme", "222", program="journalist-partners")
    plugin.store.set_cached_invite_link("-700", "https://t.me/+abc")

    plugin.store.remove_program_cascade("journalist-partners")

    assert plugin.store.get_cached_invite_link("-700") is None


# ── _extract_invite_link_from_text ───────────────────────────────────────────

def test_extract_invite_link_prefers_invite_shape_over_a_generic_link(plugin):
    text = "Welcome! Website: https://t.me/acmecorp Invite: https://t.me/+AbCdEf1234"
    assert plugin._extract_invite_link_from_text(text) == "https://t.me/+AbCdEf1234"


def test_extract_invite_link_falls_back_to_any_tme_link(plugin):
    text = "Our public channel: https://t.me/acmecorp — say hi!"
    assert plugin._extract_invite_link_from_text(text) == "https://t.me/acmecorp"


def test_extract_invite_link_returns_none_when_nothing_found(plugin):
    assert plugin._extract_invite_link_from_text("Just a plain description, no links here.") is None
    assert plugin._extract_invite_link_from_text("") is None
    assert plugin._extract_invite_link_from_text(None) is None


def test_extract_invite_link_strips_trailing_punctuation(plugin):
    text = "Join us (https://t.me/+AbCdEf1234)."
    assert plugin._extract_invite_link_from_text(text) == "https://t.me/+AbCdEf1234"


# ── _get_invite_link orchestration ───────────────────────────────────────────

def test_get_invite_link_returns_cached_value_without_any_api_call(plugin, monkeypatch):
    plugin.store.set_cached_invite_link("-700", "https://t.me/+cached")
    monkeypatch.setattr(plugin, "_create_chat_invite_link",
                         lambda chat_id: (_ for _ in ()).throw(AssertionError("should not be called")))
    monkeypatch.setattr(plugin, "_get_chat_description",
                         lambda chat_id: (_ for _ in ()).throw(AssertionError("should not be called")))

    assert plugin._get_invite_link("-700") == "https://t.me/+cached"


def test_get_invite_link_creates_and_caches_when_bot_is_admin(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "_create_chat_invite_link", lambda chat_id: "https://t.me/+fresh")

    result = plugin._get_invite_link("-700")

    assert result == "https://t.me/+fresh"
    assert plugin.store.get_cached_invite_link("-700") == "https://t.me/+fresh"


def test_get_invite_link_falls_back_to_description_when_not_admin(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "_create_chat_invite_link", lambda chat_id: None)
    monkeypatch.setattr(plugin, "_get_chat_description", lambda chat_id: "DM me: https://t.me/+fromdesc")

    result = plugin._get_invite_link("-700")

    assert result == "https://t.me/+fromdesc"
    # The description-sourced link is deliberately NOT cached (a human can
    # edit the description at any time) — nothing should be stored for it.
    assert plugin.store.get_cached_invite_link("-700") is None


def test_get_invite_link_none_when_neither_source_has_anything(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "_create_chat_invite_link", lambda chat_id: None)
    monkeypatch.setattr(plugin, "_get_chat_description", lambda chat_id: None)

    assert plugin._get_invite_link("-700") is None


def test_get_invite_link_retries_the_admin_path_on_every_uncached_call(plugin, monkeypatch):
    """A bot's admin status can change later (a human promotes it) — a
    failure must not be remembered, so the admin path keeps being retried
    until it succeeds, with no restart needed."""
    calls = []
    monkeypatch.setattr(plugin, "_create_chat_invite_link",
                         lambda chat_id: calls.append(chat_id) or None)
    monkeypatch.setattr(plugin, "_get_chat_description", lambda chat_id: None)

    plugin._get_invite_link("-700")
    plugin._get_invite_link("-700")

    assert calls == ["-700", "-700"]


# ── /hermes_invite_link command ──────────────────────────────────────────────

def test_team_member_regenerates_link_from_inside_the_client_chat(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme", "222", program="journalist-partners")
    _member(plugin, "-500", "222")  # live team-chat member, not global admin
    monkeypatch.setattr(plugin, "_create_chat_invite_link", lambda chat_id: "https://t.me/+new")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_invite_link", chat_id="-700", from_user_id="222")
    result = plugin._handle_command(msg, msg.source, "-700", "222")

    assert result == plugin._HANDLED
    assert plugin.store.get_cached_invite_link("-700") == "https://t.me/+new"
    assert sent and "https://t.me/+new" in sent[0][1]


def test_non_team_member_cannot_regenerate_link_from_the_client_chat(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme", "222", program="journalist-partners")
    # uid 333 is NOT a live member of the team chat -500.
    create_calls = []
    monkeypatch.setattr(plugin, "_create_chat_invite_link", lambda chat_id: create_calls.append(chat_id))
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_invite_link", chat_id="-700", from_user_id="333")
    result = plugin._handle_command(msg, msg.source, "-700", "333")

    assert result == plugin._NON_ADMIN_SILENT
    assert sent == []
    assert create_calls == []


def test_team_member_regenerates_link_from_the_team_chat_with_an_explicit_chat_id(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme", "222", program="journalist-partners")
    _member(plugin, "-500", "222")
    monkeypatch.setattr(plugin, "_create_chat_invite_link", lambda chat_id: "https://t.me/+new")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_invite_link -700", chat_id="-500", from_user_id="222")
    result = plugin._handle_command(msg, msg.source, "-500", "222")

    assert result == plugin._HANDLED
    assert plugin.store.get_cached_invite_link("-700") == "https://t.me/+new"


def test_team_chat_without_a_chat_id_argument_asks_for_one(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    _member(plugin, "-500", "222")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_invite_link", chat_id="-500", from_user_id="222")
    result = plugin._handle_command(msg, msg.source, "-500", "222")

    assert result == plugin._HANDLED
    assert "укажите" in sent[0][1].lower()


def test_team_chat_cannot_target_another_programs_client_chat(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.create_program("support-clients", "-600", "999")
    plugin.store.set_chat("-800", "client", "Other Co", "999", program="support-clients")
    _member(plugin, "-500", "222")
    create_calls = []
    monkeypatch.setattr(plugin, "_create_chat_invite_link", lambda chat_id: create_calls.append(chat_id))
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_invite_link -800", chat_id="-500", from_user_id="222")
    result = plugin._handle_command(msg, msg.source, "-500", "222")

    assert result == plugin._HANDLED
    assert create_calls == []
    assert "не зарегистрирован" in sent[0][1]


def test_neither_client_chat_nor_team_chat_gets_a_usage_hint(plugin, monkeypatch):
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_invite_link", chat_id="-999", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "-999", "111")

    assert result == plugin._HANDLED
    assert sent  # acknowledged with a hint, not silence — this isn't an auth refusal


def test_invite_link_command_silently_refused_from_a_dm(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    _member(plugin, "-500", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_invite_link -700", chat_id="111", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "111", "111", "dm")

    assert result == plugin._NON_ADMIN_SILENT
    assert sent == []


def test_creation_failure_reports_a_clear_error(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme", "222", program="journalist-partners")
    _member(plugin, "-500", "222")
    monkeypatch.setattr(plugin, "_create_chat_invite_link", lambda chat_id: None)
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_invite_link", chat_id="-700", from_user_id="222")
    result = plugin._handle_command(msg, msg.source, "-700", "222")

    assert result == plugin._HANDLED
    assert plugin.store.get_cached_invite_link("-700") is None
    assert "не получилось" in sent[0][1].lower()
