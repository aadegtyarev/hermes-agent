"""Tests for the "client chat" / "program" mechanism in telegram-context.

Generalizes read-only/work gating to a third pattern: isolated one-on-one
chats with an outside party (the journalist/partner and tech-support/client
use cases this was built for). A "program" is a team's own chat (journalists,
support engineers, ...); every chat registered as "client" under it is
dispatched like a work chat (normal require_mention gating decides whether a
turn fires) but — the one deliberate difference — never grants DM access,
since membership in a client chat must never let an outside partner reach the
bot in private. Registration is the single contextual ``/hermes_program
[name]`` command: creates a program (global-admin only, fresh chat) or
registers the current chat as a client under an EXISTING program (open to any
LIVE member of that program's own team chat — no separate admin list, same
trust pattern as work-chat DM auto-collection).
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
    monkeypatch.delenv("TELEGRAM_READONLY_CHATS", raising=False)
    monkeypatch.delenv("TELEGRAM_WORK_CHATS", raising=False)
    monkeypatch.delenv("TELEGRAM_HOME_CHANNEL", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    # Loads the package exactly the way hermes_cli/plugins.py's real loader
    # does (one spec, submodule_search_locations, registered at
    # sys.modules[pkg_name] itself) — see test_telegram_context_plugin.py's
    # fixture docstring for why this matters (a lazy cross-module import in
    # tools.py only resolves correctly loaded this way).
    pkg_name = f"telegram_context_programs_under_test_{next(_counter)}"
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

    # membership: {chat_id: {uid: status}} — drives the fake getChatMember.
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
        if key.startswith(pkg_name):
            del sys.modules[key]


def _member(plugin, chat_id: str, uid: str, status: str = "member") -> None:
    plugin._TEST_MEMBERSHIP.setdefault(chat_id, {})[uid] = status


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


# ── store.py: programs registry ─────────────────────────────────────────────

def test_create_program_binds_name_to_chat(plugin):
    assert plugin.store.create_program("journalist-partners", "-500", "111")
    assert plugin.store.programs() == {"journalist-partners": "-500"}
    assert plugin.store.program_chat_id("journalist-partners") == "-500"
    assert plugin.store.is_program_team_chat("-500")
    assert not plugin.store.is_program_team_chat("-999")


def test_create_program_rejects_duplicate_name(plugin):
    assert plugin.store.create_program("support-clients", "-500", "111")
    assert not plugin.store.create_program("support-clients", "-600", "222")
    assert plugin.store.program_chat_id("support-clients") == "-500"


def test_create_program_rejects_chat_already_registered_as_something_else(plugin):
    plugin.store.set_chat("-500", "work", "", "111")
    assert not plugin.store.create_program("journalist-partners", "-500", "111")


def test_create_program_rejects_reusing_another_programs_team_chat(plugin):
    plugin.store.create_program("journalist-partners", "-500", "111")
    assert not plugin.store.create_program("support-clients", "-500", "222")


def test_remove_program_by_chat(plugin):
    plugin.store.create_program("journalist-partners", "-500", "111")
    assert plugin.store.remove_program_by_chat("-500") == "journalist-partners"
    assert plugin.store.programs() == {}
    assert plugin.store.remove_program_by_chat("-500") is None


def test_set_chat_client_mode_roundtrip(plugin):
    plugin.store.set_chat("-700", "client", "Acme Corp", "111", program="journalist-partners")
    assert plugin.store.chat_mode("-700") == "client"
    assert plugin.store.chat_program("-700") == "journalist-partners"
    assert "-700" in plugin.store.chats_by_mode("client")


def test_set_chat_rejects_unknown_mode(plugin):
    plugin.store.set_chat("-700", "bogus", "", "111")
    assert plugin.store.chat_mode("-700") is None


def test_chat_program_none_for_non_client_chat(plugin):
    plugin.store.set_chat("-500", "work", "", "111")
    assert plugin.store.chat_program("-500") is None


# ── _handle_hermes_program: create ──────────────────────────────────────────

def test_admin_creates_program_in_fresh_chat(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program journalist-partners", chat_id="-500", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "-500", "111")

    assert result == plugin._HANDLED
    assert plugin.store.program_chat_id("journalist-partners") == "-500"
    assert sent and "создана" in sent[0][1]


def test_non_admin_cannot_create_program(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program journalist-partners", chat_id="-500", from_user_id="999")
    result = plugin._handle_command(msg, msg.source, "-500", "999")

    assert result == plugin._NON_ADMIN_SILENT
    assert plugin.store.programs() == {}
    assert sent == []  # total silence — no "not allowed" tell


def test_create_program_refuses_in_already_registered_chat(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    plugin.store.set_chat("-500", "work", "", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program journalist-partners", chat_id="-500", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "-500", "111")

    assert result == plugin._HANDLED
    assert plugin.store.programs() == {}
    assert "уже зарегистрирован" in sent[0][1]


# ── _handle_hermes_program: register client chat ────────────────────────────

def test_program_member_registers_client_chat(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    _member(plugin, "-500", "222")  # uid 222 is a live member of the team chat
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program journalist-partners", chat_id="-700", from_user_id="222")
    result = plugin._handle_command(msg, msg.source, "-700", "222")

    assert result == plugin._HANDLED
    assert plugin.store.chat_mode("-700") == "client"
    assert plugin.store.chat_program("-700") == "journalist-partners"
    # Notified both the new chat and the program's own team chat.
    chat_ids_sent = {c for c, _ in sent}
    assert "-700" in chat_ids_sent and "-500" in chat_ids_sent


def test_non_member_cannot_register_client_chat_under_existing_program(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program journalist-partners", chat_id="-700", from_user_id="333")
    result = plugin._handle_command(msg, msg.source, "-700", "333")

    assert result == plugin._NON_ADMIN_SILENT
    assert plugin.store.chat_mode("-700") is None
    assert sent == []


def test_client_chat_registration_does_not_require_global_admin(plugin, monkeypatch):
    """The whole point of per-program rights: a program member who is NOT in
    TELEGRAM_ADMIN_USERS can still register client chats under their own
    program — only creating a brand-new program needs global admin."""
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "999999")  # 222 is NOT a global admin
    plugin.store.create_program("journalist-partners", "-500", "111")
    _member(plugin, "-500", "222")

    msg = _group_message("/hermes_program journalist-partners", chat_id="-700", from_user_id="222")
    result = plugin._handle_command(msg, msg.source, "-700", "222")

    assert result == plugin._HANDLED
    assert plugin.store.chat_mode("-700") == "client"


# ── _handle_hermes_program: auto-infer program from membership ──────────────

def test_auto_infers_single_program_membership(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    _member(plugin, "-500", "222")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program", chat_id="-700", from_user_id="222")
    result = plugin._handle_command(msg, msg.source, "-700", "222")

    assert result == plugin._HANDLED
    assert plugin.store.chat_program("-700") == "journalist-partners"


def test_ambiguous_membership_asks_to_disambiguate_without_registering(plugin, monkeypatch):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.create_program("support-clients", "-600", "111")
    _member(plugin, "-500", "222")
    _member(plugin, "-600", "222")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program", chat_id="-700", from_user_id="222")
    result = plugin._handle_command(msg, msg.source, "-700", "222")

    assert result == plugin._HANDLED
    assert plugin.store.chat_mode("-700") is None
    assert "укажите явно" in sent[0][1].lower()


def test_no_membership_and_not_admin_is_silent(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program", chat_id="-700", from_user_id="999")
    result = plugin._handle_command(msg, msg.source, "-700", "999")

    assert result == plugin._NON_ADMIN_SILENT
    assert sent == []


def test_no_membership_but_admin_is_prompted_for_a_name(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program", chat_id="-700", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "-700", "111")

    assert result == plugin._HANDLED
    assert plugin.store.programs() == {}
    assert "укажите" in sent[0][1].lower()


# ── /hermes_forget on a program's team chat ─────────────────────────────────

def test_hermes_forget_removes_a_program(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    plugin.store.create_program("journalist-partners", "-500", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_forget", chat_id="-500", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "-500", "111")

    assert result == plugin._HANDLED
    assert plugin.store.programs() == {}


def test_hermes_forget_clears_orphaned_per_chat_state(plugin, monkeypatch):
    """Without this, re-registering the SAME chat_id later (a new client
    chat, possibly under a different program) would inherit the PREVIOUS
    occupant's gdoc allowlist — a new partner's chat trusting a document an
    unrelated prior chat once linked."""
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme", "111", program="journalist-partners")
    plugin.store.link_chat_doc("-700", "SOME_DOC")
    plugin.store.record_escalation("-700")
    plugin.store.mark_reviewed("-700")
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: None)

    msg = _group_message("/hermes_forget", chat_id="-700", from_user_id="111")
    plugin._handle_command(msg, msg.source, "-700", "111")

    assert plugin.store.chat_doc_ids("-700") == set()
    assert plugin.store.last_escalation_ts("-700") is None
    assert plugin.store.last_reviewed_ts("-700") is None


# ── /hermes_program is group-chat only, not usable from a DM ────────────────

def test_hermes_program_silently_refused_from_a_dm(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program journalist-partners", chat_id="111", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "111", "111", "dm")

    assert result == plugin._NON_ADMIN_SILENT
    assert plugin.store.programs() == {}
    assert sent == []


def test_hermes_program_works_normally_outside_a_dm(plugin, monkeypatch):
    """Regression guard: the DM refusal must not accidentally catch group
    chats too — ctype defaults to "" when callers (including every other
    test in this file) don't pass it, which must behave like "not a DM"."""
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")

    msg = _group_message("/hermes_program journalist-partners", chat_id="-500", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "-500", "111")

    assert result == plugin._HANDLED
    assert plugin.store.program_chat_id("journalist-partners") == "-500"


# ── Program name validation/normalization ───────────────────────────────────

def test_program_name_is_case_insensitive_for_matching(plugin, monkeypatch):
    """Without normalizing case once up front, "/hermes_program Foo" and an
    existing "foo" would be treated as two different programs."""
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    plugin.store.create_program("journalist-partners", "-500", "111")
    _member(plugin, "-500", "222")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program Journalist-Partners", chat_id="-700", from_user_id="222")
    result = plugin._handle_command(msg, msg.source, "-700", "222")

    assert result == plugin._HANDLED
    assert plugin.store.chat_program("-700") == "journalist-partners"


def test_create_program_rejects_a_name_with_spaces(plugin):
    assert not plugin.store.create_program("my team", "-500", "111")
    assert plugin.store.programs() == {}


def test_create_program_rejects_empty_and_punctuation_only_names(plugin):
    assert not plugin.store.create_program("", "-500", "111")
    assert not plugin.store.create_program("!!!", "-500", "111")


# ── _on_dispatch: client-mode chats behave like work, minus DM access ───────

def test_client_chat_dispatches_normally_but_grants_no_dm_access(plugin):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme", "222", program="journalist-partners")

    msg = _group_message("hello, question about the article", chat_id="-700", from_user_id="333")
    result = plugin._on_dispatch(event=msg, gateway=None, session_store=None)

    assert result is None  # normal dispatch — core require_mention decides the rest
    assert not plugin.store.is_dm_allowed("333")


def test_client_chat_messages_are_ingested(plugin):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme", "222", program="journalist-partners")

    msg = _group_message("here are the photos from the site visit", chat_id="-700", from_user_id="333")
    plugin._on_dispatch(event=msg, gateway=None, session_store=None)

    page = plugin.store.recent("-700", 10)
    assert [m["text"] for m in page["messages"]] == ["here are the photos from the site visit"]


def test_work_chat_membership_unaffected_by_client_chats_existing(plugin):
    """Regression guard: adding client-chat support must not change work-chat
    behavior (DM access still granted there)."""
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme", "222", program="journalist-partners")
    plugin.store.set_chat("-800", "work", "", "111")

    msg = _group_message("hi", chat_id="-800", from_user_id="444")
    plugin._on_dispatch(event=msg, gateway=None, session_store=None)

    assert plugin.store.is_dm_allowed("444")


def test_program_team_chat_responds_like_a_work_chat(plugin):
    """The program's own team chat (lives only in partner_programs, never in
    chats_allowed) must dispatch like a work chat — the team talks to the bot
    there and escalations land there — not fall through to the final skip."""
    # Some OTHER chats are configured, so the "unconfigured — allow all" branch
    # does not apply; the team chat must match on its own.
    plugin.store.set_chat("-900", "work", "", "111")
    plugin.store.create_program("journalist-partners", "-500", "111")

    msg = _group_message("что у нас сегодня в клиентских чатиках было?",
                         chat_id="-500", from_user_id="111")
    result = plugin._on_dispatch(event=msg, gateway=None, session_store=None)

    assert result is None  # responds (core require_mention still gates the turn)
    assert plugin.store.is_dm_allowed("111")  # team member gets DM access, like work


def test_program_team_chat_messages_are_ingested(plugin):
    plugin.store.set_chat("-900", "work", "", "111")
    plugin.store.create_program("journalist-partners", "-500", "111")

    msg = _group_message("team note", chat_id="-500", from_user_id="111")
    plugin._on_dispatch(event=msg, gateway=None, session_store=None)

    page = plugin.store.recent("-500", 10)
    assert [m["text"] for m in page["messages"]] == ["team note"]


# ── store.py: rename_program / move_program_chat / remove_program_cascade ──

def test_rename_program_carries_client_chats_along(plugin):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme", "222", program="journalist-partners")

    assert plugin.store.rename_program("journalist-partners", "press-partners")

    assert plugin.store.programs() == {"press-partners": "-500"}
    assert plugin.store.chat_program("-700") == "press-partners"


def test_rename_program_rejects_unknown_old_name(plugin):
    assert not plugin.store.rename_program("ghost", "new-name")


def test_rename_program_rejects_name_already_taken(plugin):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.create_program("support-clients", "-600", "111")
    assert not plugin.store.rename_program("journalist-partners", "support-clients")
    assert plugin.store.program_chat_id("journalist-partners") == "-500"


def test_rename_program_rejects_invalid_new_name(plugin):
    plugin.store.create_program("journalist-partners", "-500", "111")
    assert not plugin.store.rename_program("journalist-partners", "has spaces")
    assert plugin.store.programs() == {"journalist-partners": "-500"}


def test_move_program_chat_repoints_team_chat_only(plugin):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme", "222", program="journalist-partners")

    assert plugin.store.move_program_chat("journalist-partners", "-900")

    assert plugin.store.program_chat_id("journalist-partners") == "-900"
    assert plugin.store.is_program_team_chat("-900")
    assert not plugin.store.is_program_team_chat("-500")
    assert plugin.store.chat_program("-700") == "journalist-partners"  # untouched


def test_move_program_chat_rejects_unknown_program(plugin):
    assert not plugin.store.move_program_chat("ghost", "-900")


def test_move_program_chat_rejects_target_already_registered(plugin):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-900", "work", "", "111")
    assert not plugin.store.move_program_chat("journalist-partners", "-900")
    assert plugin.store.program_chat_id("journalist-partners") == "-500"


def test_remove_program_cascade_drops_program_and_its_client_chats(plugin):
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme", "222", program="journalist-partners")
    plugin.store.set_chat("-800", "client", "Globex", "222", program="journalist-partners")
    plugin.store.link_chat_doc("-700", "SOME_DOC")

    removed = plugin.store.remove_program_cascade("journalist-partners")

    assert set(removed) == {"-700", "-800"}
    assert plugin.store.programs() == {}
    assert plugin.store.chat_mode("-700") is None
    assert plugin.store.chat_mode("-800") is None
    assert plugin.store.chat_doc_ids("-700") == set()


def test_remove_program_cascade_unknown_program_is_noop(plugin):
    assert plugin.store.remove_program_cascade("ghost") == []


# ── /hermes_program_rename ───────────────────────────────────────────────────

def test_admin_renames_program_from_its_team_chat(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    plugin.store.create_program("journalist-partners", "-500", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program_rename press-partners", chat_id="-500", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "-500", "111")

    assert result == plugin._HANDLED
    assert plugin.store.programs() == {"press-partners": "-500"}
    assert "переименована" in sent[0][1]


def test_non_admin_cannot_rename_program(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    plugin.store.create_program("journalist-partners", "-500", "111")
    _member(plugin, "-500", "222")  # a live team member, but not a global admin
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program_rename press-partners", chat_id="-500", from_user_id="222")
    result = plugin._handle_command(msg, msg.source, "-500", "222")

    assert result == plugin._NON_ADMIN_SILENT
    assert plugin.store.programs() == {"journalist-partners": "-500"}
    assert sent == []


def test_rename_outside_a_team_chat_is_rejected(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program_rename press-partners", chat_id="-999", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "-999", "111")

    assert result == plugin._HANDLED
    assert "только внутри чата команды" in sent[0][1]


def test_rename_silently_refused_from_a_dm(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    plugin.store.create_program("journalist-partners", "-500", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program_rename press-partners", chat_id="-500", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "-500", "111", "dm")

    assert result == plugin._NON_ADMIN_SILENT
    assert plugin.store.programs() == {"journalist-partners": "-500"}
    assert sent == []


# ── /hermes_program_move ─────────────────────────────────────────────────────

def test_admin_moves_program_to_a_fresh_chat(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    plugin.store.create_program("journalist-partners", "-500", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program_move journalist-partners", chat_id="-900", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "-900", "111")

    assert result == plugin._HANDLED
    assert plugin.store.program_chat_id("journalist-partners") == "-900"
    chat_ids_sent = {c for c, _ in sent}
    assert "-900" in chat_ids_sent and "-500" in chat_ids_sent  # new + old team chat notified


def test_move_refuses_an_already_registered_target_chat(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-900", "work", "", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program_move journalist-partners", chat_id="-900", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "-900", "111")

    assert result == plugin._HANDLED
    assert plugin.store.program_chat_id("journalist-partners") == "-500"
    assert "уже зарегистрирован" in sent[0][1]


def test_move_unknown_program_name_reports_not_found(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program_move ghost", chat_id="-900", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "-900", "111")

    assert result == plugin._HANDLED
    assert "не найдена" in sent[0][1]


def test_non_admin_cannot_move_program(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    plugin.store.create_program("journalist-partners", "-500", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_program_move journalist-partners", chat_id="-900", from_user_id="222")
    result = plugin._handle_command(msg, msg.source, "-900", "222")

    assert result == plugin._NON_ADMIN_SILENT
    assert plugin.store.program_chat_id("journalist-partners") == "-500"
    assert sent == []


# ── /hermes_forget: cascade-with-confirm on a program's team chat ───────────

def test_forget_team_chat_with_no_clients_deletes_immediately(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    plugin.store.create_program("journalist-partners", "-500", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_forget", chat_id="-500", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "-500", "111")

    assert result == plugin._HANDLED
    assert plugin.store.programs() == {}
    assert "удалена" in sent[0][1]


def test_forget_team_chat_with_clients_asks_for_confirmation_first(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme", "222", program="journalist-partners")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_forget", chat_id="-500", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "-500", "111")

    assert result == plugin._HANDLED
    # Nothing was actually deleted yet.
    assert plugin.store.programs() == {"journalist-partners": "-500"}
    assert plugin.store.chat_mode("-700") == "client"
    assert "confirm" in sent[0][1]


def test_forget_confirm_cascades_to_every_client_chat(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    plugin.store.create_program("journalist-partners", "-500", "111")
    plugin.store.set_chat("-700", "client", "Acme", "222", program="journalist-partners")
    plugin.store.set_chat("-800", "client", "Globex", "222", program="journalist-partners")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))

    msg = _group_message("/hermes_forget confirm", chat_id="-500", from_user_id="111")
    result = plugin._handle_command(msg, msg.source, "-500", "111")

    assert result == plugin._HANDLED
    assert plugin.store.programs() == {}
    assert plugin.store.chat_mode("-700") is None
    assert plugin.store.chat_mode("-800") is None
