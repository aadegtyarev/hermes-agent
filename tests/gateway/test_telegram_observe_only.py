"""Tests for read-only (``observe_only``) Telegram chats.

A chat listed in ``observe_only_chats`` is READ — every message is ingested as
context — but the bot must NEVER emit anything to it. This is enforced at:
  * the inbound dispatch gate (``_should_process_message`` returns False),
  * the observe gate (``_should_observe_unmentioned_group_message`` returns
    True regardless of the observe flag / allowlists / addressing),
  * every outbound Bot API chokepoint in the adapter, and
  * the out-of-adapter ``send_message`` / cron path (``_send_to_platform``).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.telegram import TelegramAdapter

OBSERVE_CHAT = "-1009876543210"


def _make_adapter(observe_only=None, **extra):
    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    cfg_extra = dict(extra)
    if observe_only is not None:
        cfg_extra["observe_only_chats"] = observe_only
    adapter.config = PlatformConfig(enabled=True, token="fake-token", extra=cfg_extra)
    adapter._bot = AsyncMock()
    adapter._bot.set_message_reaction = AsyncMock()
    adapter._bot.send_chat_action = AsyncMock()
    return adapter


def _msg(chat_id=OBSERVE_CHAT, chat_type="supergroup", thread_id=None,
         reply_to_message=None, entities=None):
    return SimpleNamespace(
        text="hello",
        caption=None,
        entities=entities or [],
        caption_entities=[],
        message_thread_id=thread_id,
        chat=SimpleNamespace(id=int(chat_id), type=chat_type, is_forum=False),
        reply_to_message=reply_to_message,
    )


# ── config parsing ───────────────────────────────────────────────────


def test_observe_only_chats_parses_list(monkeypatch):
    monkeypatch.delenv("TELEGRAM_OBSERVE_ONLY_CHATS", raising=False)
    a = _make_adapter(observe_only=["-100123", " -100456 "])
    assert a._telegram_observe_only_chats() == {"-100123", "-100456"}


def test_observe_only_chats_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_OBSERVE_ONLY_CHATS", "-100777,-100888")
    a = _make_adapter()
    assert a._telegram_observe_only_chats() == {"-100777", "-100888"}


def test_is_observe_only_chat(monkeypatch):
    monkeypatch.delenv("TELEGRAM_OBSERVE_ONLY_CHATS", raising=False)
    a = _make_adapter(observe_only=["-100123"])
    assert a._is_observe_only_chat("-100123") is True
    assert a._is_observe_only_chat(-100123) is True  # int coerced to str
    assert a._is_observe_only_chat("-100999") is False
    assert a._is_observe_only_chat("") is False


# ── inbound: never dispatch, always ingest ───────────────────────────


def test_observe_only_never_dispatches(monkeypatch):
    """Even a reply-to-bot in an observe-only chat must not trigger the agent."""
    monkeypatch.delenv("TELEGRAM_OBSERVE_ONLY_CHATS", raising=False)
    a = _make_adapter(observe_only=[OBSERVE_CHAT])
    msg = _msg(reply_to_message=SimpleNamespace(from_user=SimpleNamespace(id=999)))
    assert a._should_process_message(msg) is False


def test_non_observe_group_still_dispatches(monkeypatch):
    """A normal group (require_mention off) is unaffected by the feature."""
    for var in (
        "TELEGRAM_OBSERVE_ONLY_CHATS", "TELEGRAM_ALLOWED_CHATS",
        "TELEGRAM_REQUIRE_MENTION", "TELEGRAM_GUEST_MODE",
        "TELEGRAM_FREE_RESPONSE_CHATS", "TELEGRAM_EXCLUSIVE_BOT_MENTIONS",
    ):
        monkeypatch.delenv(var, raising=False)
    # Pin response-gating knobs in config.extra so the result does not depend
    # on ambient env left by other tests in a full-suite run.
    a = _make_adapter(
        observe_only=[OBSERVE_CHAT], require_mention=False, allowed_chats=[],
    )
    a._bot = SimpleNamespace(id=999, username="hermes_bot")
    assert a._should_process_message(_msg(chat_id="-100999")) is True


def test_observe_only_ingested_without_flag_or_allowlist(monkeypatch):
    """Observe-only ingests even when observe_unmentioned is off and the chat
    is not in allowed_chats — independent of the normal observe machinery."""
    monkeypatch.delenv("TELEGRAM_OBSERVE_ONLY_CHATS", raising=False)
    monkeypatch.delenv("TELEGRAM_OBSERVE_UNMENTIONED_GROUP_MESSAGES", raising=False)
    a = _make_adapter(observe_only=[OBSERVE_CHAT])
    assert a._should_observe_unmentioned_group_message(_msg()) is True


def test_observe_only_respects_ignored_threads(monkeypatch):
    monkeypatch.delenv("TELEGRAM_OBSERVE_ONLY_CHATS", raising=False)
    a = _make_adapter(observe_only=[OBSERVE_CHAT], ignored_threads="55")
    assert a._should_observe_unmentioned_group_message(_msg(thread_id=55)) is False


def test_non_observe_not_ingested_without_flag(monkeypatch):
    monkeypatch.delenv("TELEGRAM_OBSERVE_ONLY_CHATS", raising=False)
    monkeypatch.delenv("TELEGRAM_OBSERVE_UNMENTIONED_GROUP_MESSAGES", raising=False)
    a = _make_adapter(observe_only=[OBSERVE_CHAT])
    assert a._should_observe_unmentioned_group_message(_msg(chat_id="-100999")) is False


# ── outbound: every chokepoint hard-blocked ──────────────────────────


@pytest.mark.asyncio
async def test_send_blocked_in_observe_only(monkeypatch):
    monkeypatch.delenv("TELEGRAM_OBSERVE_ONLY_CHATS", raising=False)
    a = _make_adapter(observe_only=[OBSERVE_CHAT])
    res = await a.send(OBSERVE_CHAT, "hi")
    assert res.success is False
    assert res.error == "observe_only_chat"


@pytest.mark.asyncio
async def test_send_not_blocked_elsewhere(monkeypatch):
    """A non-observe chat passes the guard (here it then hits Not connected)."""
    monkeypatch.delenv("TELEGRAM_OBSERVE_ONLY_CHATS", raising=False)
    a = _make_adapter(observe_only=[OBSERVE_CHAT])
    a._bot = None  # guard runs before the _bot check, so this proves it didn't fire
    res = await a.send("-100999", "hi")
    assert res.success is False
    assert res.error == "Not connected"


@pytest.mark.asyncio
async def test_edit_blocked_in_observe_only(monkeypatch):
    monkeypatch.delenv("TELEGRAM_OBSERVE_ONLY_CHATS", raising=False)
    a = _make_adapter(observe_only=[OBSERVE_CHAT])
    res = await a.edit_message(OBSERVE_CHAT, "55", "hi")
    assert res.success is False
    assert res.error == "observe_only_chat"


@pytest.mark.asyncio
async def test_draft_blocked_in_observe_only(monkeypatch):
    monkeypatch.delenv("TELEGRAM_OBSERVE_ONLY_CHATS", raising=False)
    a = _make_adapter(observe_only=[OBSERVE_CHAT])
    res = await a.send_draft(OBSERVE_CHAT, 1, "hi")
    assert res.success is False
    assert res.error == "observe_only_chat"


@pytest.mark.asyncio
async def test_typing_blocked_in_observe_only(monkeypatch):
    monkeypatch.delenv("TELEGRAM_OBSERVE_ONLY_CHATS", raising=False)
    a = _make_adapter(observe_only=[OBSERVE_CHAT])
    await a.send_typing(OBSERVE_CHAT)
    a._bot.send_chat_action.assert_not_called()


@pytest.mark.asyncio
async def test_set_reaction_blocked_in_observe_only(monkeypatch):
    monkeypatch.delenv("TELEGRAM_OBSERVE_ONLY_CHATS", raising=False)
    a = _make_adapter(observe_only=[OBSERVE_CHAT])
    res = await a._set_reaction(OBSERVE_CHAT, "55", "\U0001f440")
    assert res is False
    a._bot.set_message_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_clear_reactions_blocked_in_observe_only(monkeypatch):
    monkeypatch.delenv("TELEGRAM_OBSERVE_ONLY_CHATS", raising=False)
    a = _make_adapter(observe_only=[OBSERVE_CHAT])
    res = await a._clear_reactions(OBSERVE_CHAT, "55")
    assert res is False
    a._bot.set_message_reaction.assert_not_called()


# ── out-of-adapter path: send_message tool + cron delivery ───────────


def test_tool_is_observe_only_telegram_chat(monkeypatch):
    from tools.send_message_tool import _is_observe_only_telegram_chat
    monkeypatch.delenv("TELEGRAM_OBSERVE_ONLY_CHATS", raising=False)
    pconfig = SimpleNamespace(extra={"observe_only_chats": ["-100123"]})
    assert _is_observe_only_telegram_chat(pconfig, "-100123") is True
    assert _is_observe_only_telegram_chat(pconfig, "-100999") is False


def test_tool_is_observe_only_from_env(monkeypatch):
    from tools.send_message_tool import _is_observe_only_telegram_chat
    monkeypatch.setenv("TELEGRAM_OBSERVE_ONLY_CHATS", "-100555")
    pconfig = SimpleNamespace(extra={})
    assert _is_observe_only_telegram_chat(pconfig, "-100555") is True


@pytest.mark.asyncio
async def test_send_to_platform_blocks_observe_only(monkeypatch):
    from tools.send_message_tool import _send_to_platform
    monkeypatch.delenv("TELEGRAM_OBSERVE_ONLY_CHATS", raising=False)
    pconfig = SimpleNamespace(extra={"observe_only_chats": ["-100123"]})
    res = await _send_to_platform(Platform.TELEGRAM, pconfig, "-100123", "hi")
    assert isinstance(res, dict)
    assert "error" in res
    assert "observe_only" in res["error"]
