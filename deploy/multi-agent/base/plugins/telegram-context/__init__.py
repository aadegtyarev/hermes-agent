"""Telegram context plugin — ingest + chat/user gating + thread reconstruction.

One `pre_gateway_dispatch` hook does four things for Telegram:
  1. Chat gating — the bot ENGAGES only in configured chats; added elsewhere → ignored.
       TELEGRAM_WORK_CHATS      → bot responds here; posters auto-added to the DM allowlist.
       TELEGRAM_READONLY_CHATS  → ingested for context, but the bot never replies (observe-only).
       (unset work+readonly → allow everything, so setup isn't locked out.)
  2. DM allowlist — a DM is answered only if the sender is allowlisted: a work-chat
     member (auto-collected + live getChatMember check) or TELEGRAM_DM_EXTRA_USERS.
     Members of the read-only public chat do NOT gain DM access.
  3. Auto-pairing — a confirmed work-chat member (posted there, or live getChatMember
     lookup on DM) is written straight into the REAL gateway.pairing.PairingStore
     (the same approved-list `hermes pairing list`/`_is_user_authorized` reads), not
     just this plugin's own bookkeeping. This is what actually grants core-level
     authorization in BOTH group and DM contexts, with no operator approval step —
     membership in an enrolled chat IS the trust decision. A stranger who is not a
     member of any work chat still gets silently dropped in DM, same as before: no
     pairing code is ever shown to them (the plugin's own gate runs before the
     gateway's default "here's your pairing code" flow, so that flow never fires for
     non-members here).
  4. Ingest — stores messages so telegram_thread/recent/search can read history the
     Bot API can't fetch.

A separate ``telegram_chat_member_left`` hook (fired by the core Telegram adapter
on the legacy ``message.left_chat_member`` service field — works for any bot,
no admin rights needed) mirrors auto-pairing on the way out: when a member
leaves/is removed from a work chat, their access is revoked from BOTH this
plugin's own store AND the real PairingStore, UNLESS they're still a live
member of another enrolled work chat (checked before revoking, so belonging to
several work chats survives leaving just one).

The work/read-only chat allowlist is ENV ∪ a runtime store: a bot operator listed in
TELEGRAM_ADMIN_USERS can enrol the current chat with a command — no file edits:
  /hermes_here      → add this chat as work (bot responds)
  /hermes_readonly  → add this chat as read-only (observe only)
  /hermes_forget    → drop this chat from the runtime list
  /hermes_chats     → show the runtime list
Commands are handled in the hook (before gating, so they work in a not-yet-enrolled
chat), acknowledged via Bot API, and never forwarded to the agent. Commands reach the
bot even with group privacy mode on (`/cmd@Bot`); full ingest still needs privacy off.

For auto-collection to work, leave the gateway's own TELEGRAM_ALLOWED_USERS empty
(this hook is the gate). Opt-in via plugins.enabled: [telegram-context] + toolset `telegram`.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request

from . import store, tools as T

logger = logging.getLogger(__name__)
_MEMBER_STATUSES = {"creator", "administrator", "member", "restricted"}


def _csv(name: str) -> set[str]:
    return {x.strip() for x in os.environ.get(name, "").split(",") if x.strip()}


def _store_chats(mode: str) -> set[str]:
    try:
        return store.chats_by_mode(mode)
    except Exception:  # fail-safe: fall back to env-only, never lock up the gate
        return set()


def _work_chats() -> set[str]:
    return _csv("TELEGRAM_WORK_CHATS") | _store_chats("work")


def _readonly_chats() -> set[str]:
    return _csv("TELEGRAM_READONLY_CHATS") | _store_chats("readonly")


def _admin_users() -> set[str]:
    return _csv("TELEGRAM_ADMIN_USERS")


def _send(chat_id: str, text: str) -> None:
    """Send a plain-text reply via the Bot API (used to ack admin commands)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not chat_id:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=8)
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram-context sendMessage failed: %s", e)


_CHAT_COMMANDS = {"/hermes_here", "/hermes_readonly", "/hermes_forget", "/hermes_chats"}

# Menu descriptions for the /hermes_* admin commands. Registering them as plugin
# slash commands makes them show up in Telegram's "/" menu (private chats — the
# group menu is intentionally blanked, see _clear_group_command_menu). The real
# work stays in the pre_gateway_dispatch hook, which fires before auth and
# short-circuits these; the handler below is only a fallback for contexts where
# that hook doesn't run.
_MENU_COMMANDS = (
    ("hermes_here", "Сделать этот чат рабочим (бот отвечает)"),
    ("hermes_readonly", "Сделать чат read-only (только наблюдение)"),
    ("hermes_forget", "Убрать этот чат из списка"),
    ("hermes_chats", "Показать список чатов"),
)


def _menu_command_fallback(_raw_args: str = "") -> str:
    return ("Команда управляет списком чатов Telegram — её обрабатывает гейтвей "
            "в самом чате; вызывает её оператор бота.")


_last_group_menu_clear = 0.0
_GROUP_MENU_CLEAR_INTERVAL = 600  # сек: не чаще раза в 10 мин


def _clear_group_command_menu() -> None:
    """Держит меню «/» в групповых чатах пустым (троттлинг, best-effort).

    Адаптер платформы регистрирует полное меню во все скоупы при старте (и на
    каждом реконнекте). Здесь очищаем скоуп AllGroupChats, чтобы участники группы
    не видели список команд, которые им всё равно недоступны — слэш-доступ гейтится
    (group_allow_admin_from). Троттлинг: после реконнекта, вернувшего меню, оно
    снова обнулится в пределах интервала, без Bot API-вызова на каждое сообщение.
    Тот же транспорт, что и у отправки ack."""
    global _last_group_menu_clear
    now = time.time()
    if now - _last_group_menu_clear < _GROUP_MENU_CLEAR_INTERVAL:
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return
    try:
        payload = urllib.parse.urlencode({
            "commands": json.dumps([]),
            "scope": json.dumps({"type": "all_group_chats"}),
        }).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/setMyCommands", data=payload, timeout=8)
        _last_group_menu_clear = now
        logger.info("telegram-context: cleared group-scope command menu")
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram-context: failed to clear group menu: %s", e)


def _handle_command(event, src, chat_id: str, uid: str):
    """If the message is a /hermes_* chat-admin command, act on it and return a skip
    action (so it isn't forwarded to the agent). Returns None if not a command."""
    text = (getattr(event, "text", "") or "").strip()
    if not text.startswith("/hermes_"):
        return None
    cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()  # strip @BotUsername
    if cmd not in _CHAT_COMMANDS:
        return None
    if uid not in _admin_users():
        _send(chat_id, "⛔ Управлять списком чатов может только оператор бота (TELEGRAM_ADMIN_USERS).")
        return {"action": "skip", "reason": "telegram chat command from non-admin"}
    title = getattr(src, "chat_name", "") or ""
    if cmd == "/hermes_here":
        store.set_chat(chat_id, "work", title, uid)
        _send(chat_id, "✅ Чат добавлен как рабочий — отвечаю здесь.")
    elif cmd == "/hermes_readonly":
        store.set_chat(chat_id, "readonly", title, uid)
        _send(chat_id, "👀 Чат добавлен как read-only — читаю для контекста, не отвечаю.")
    elif cmd == "/hermes_forget":
        removed = store.remove_chat(chat_id)
        _send(chat_id, "🗑 Чат убран из списка." if removed
              else "Этого чата нет в динамическом списке (возможно, он задан через .env).")
    elif cmd == "/hermes_chats":
        rows = store.list_chats()
        if rows:
            lines = [f"• {r['mode']}: {r['chat_id']}" + (f" — {r['title']}" if r.get("title") else "")
                     for r in rows]
            _send(chat_id, "Динамический список чатов:\n" + "\n".join(lines))
        else:
            _send(chat_id, "Динамический список пуст (чаты также могут быть заданы через .env).")
    return {"action": "skip", "reason": "telegram chat command handled"}


def _ingest(event) -> None:
    src = getattr(event, "source", None)
    if src is None:
        return
    store.add({
        "chat_id": str(getattr(src, "chat_id", "") or ""),
        "message_id": str(getattr(event, "message_id", "") or ""),
        "ts": time.time(),
        "user_id": str(getattr(src, "user_id", "") or ""),
        "user_name": getattr(src, "user_name", "") or "",
        "chat_type": getattr(src, "chat_type", "") or "",
        "chat_name": getattr(src, "chat_name", "") or "",
        "thread_id": str(getattr(src, "thread_id", "") or ""),
        "text": getattr(event, "text", "") or "",
        "reply_to_message_id": str(getattr(event, "reply_to_message_id", "") or ""),
        "reply_to_author": getattr(event, "reply_to_author_name", "") or "",
    })


def _auto_approve_pairing(uid: str, user_name: str = "") -> None:
    """Grant real, core-recognized authorization to a confirmed work-chat member.

    Writes straight into gateway.pairing.PairingStore's approved list — the same
    file ``hermes pairing list`` shows and ``authz_mixin._is_user_authorized``
    reads — so membership in an enrolled chat is immediately sufficient in BOTH
    group and DM contexts, with no operator ``hermes pairing approve`` step.

    Self-guarded via ``is_approved`` (cheap read) rather than relying on the
    caller having tracked "first sight" — this plugin's own dm_allowed table
    predates real pairing-store integration, so a user already marked
    dm_allowed from before this feature shipped would never get backfilled
    into PairingStore if callers only invoked this on a dm_allowed transition.
    Checking here instead means it's safe (and correct) to call on every
    message from a work-chat member, not just the first one this plugin has
    ever seen.
    """
    if not uid:
        return
    try:
        from gateway.pairing import PairingStore
        ps = PairingStore()
        if ps.is_approved("telegram", uid):
            return
        with ps._lock:
            ps._approve_user("telegram", uid, user_name)
        logger.info("telegram-context: auto-approved uid=%s", uid)
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram-context: auto-approve failed for uid=%s: %s", uid, e)


def _live_member(uid: str) -> bool:
    """Confirm the user is a member of any work chat via getChatMember (cached on hit)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not uid:
        return False
    for chat in _work_chats():
        try:
            url = (f"https://api.telegram.org/bot{token}/getChatMember"
                   f"?chat_id={urllib.parse.quote(chat)}&user_id={uid}")
            with urllib.request.urlopen(url, timeout=8) as r:
                data = json.loads(r.read().decode())
            if (data.get("result") or {}).get("status") in _MEMBER_STATUSES:
                store.add_dm_user(uid, source_chat=chat)
                _auto_approve_pairing(uid)
                return True
        except Exception:
            continue
    return False


def _dm_allowed(uid: str) -> bool:
    if not uid:
        return False
    if uid in _csv("TELEGRAM_DM_EXTRA_USERS"):
        return True
    if store.is_dm_allowed(uid):
        return True
    return _live_member(uid)


def _on_dispatch(event=None, gateway=None, session_store=None, **kwargs):
    try:
        src = getattr(event, "source", None)
        if src is None or "telegram" not in str(getattr(src, "platform", "")).lower():
            return None
        _clear_group_command_menu()  # keep the group "/" menu blank (throttled)
        chat_id = str(getattr(src, "chat_id", "") or "")
        ctype = (getattr(src, "chat_type", "") or "").lower()
        uid = str(getattr(src, "user_id", "") or "")

        # Admin chat-management commands run before gating, so they work even in a
        # chat that isn't enrolled yet (otherwise the gate would skip them first).
        cmd_result = _handle_command(event, src, chat_id, uid)
        if cmd_result is not None:
            return cmd_result

        work, ro = _work_chats(), _readonly_chats()

        if ctype == "dm":
            _ingest(event)
            if _dm_allowed(uid):
                _auto_approve_pairing(uid, getattr(src, "user_name", "") or "")
                return None
            return {"action": "skip", "reason": "DM sender not in the auto-collected allowlist"}

        # group / channel / thread
        if chat_id in work:
            _ingest(event)
            if uid:
                store.add_dm_user(uid, getattr(src, "user_name", "") or "", chat_id)
                _auto_approve_pairing(uid, getattr(src, "user_name", "") or "")
            return None
        if chat_id in ro:
            _ingest(event)
            return {"action": "skip", "reason": "read-only chat (observe only)"}
        if not work and not ro:
            _ingest(event)     # unconfigured — don't lock out during setup
            return None
        return {"action": "skip", "reason": "chat not in TELEGRAM_WORK_CHATS/READONLY_CHATS"}
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram-context hook error: %s", e)
        return None


def _on_chat_member_left(chat_id=None, user_id=None, **kwargs) -> None:
    """Revoke auto-granted pairing when a member leaves an enrolled work chat.

    No-op for chats we don't treat as a trust source (readonly / unenrolled) —
    those never granted access via auto-pairing in the first place. If the
    user is still a live member of ANOTHER enrolled work chat (checked via the
    same getChatMember lookup _live_member uses), access is kept — leaving one
    of several work chats doesn't revoke.
    """
    chat_id = str(chat_id or "")
    uid = str(user_id or "")
    if not uid or chat_id not in _work_chats():
        return
    if _live_member(uid):
        return
    store.remove_dm_user(uid)
    try:
        from gateway.pairing import PairingStore
        ps = PairingStore()
        with ps._lock:
            ps.revoke("telegram", uid)
        logger.info("telegram-context: revoked pairing for uid=%s (left chat %s)", uid, chat_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram-context: revoke failed for uid=%s: %s", uid, e)


def register(ctx) -> None:
    try:
        store.init()
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram-context store init failed: %s", e)
    for name, schema, handler, emoji in T.TOOLS:
        ctx.register_tool(name=name, toolset="telegram", schema=schema, handler=handler, emoji=emoji)
    ctx.register_hook("pre_gateway_dispatch", _on_dispatch)
    ctx.register_hook("telegram_chat_member_left", _on_chat_member_left)
    # Surface the /hermes_* admin commands in Telegram's "/" menu. Handling stays
    # in the hook above (fires before auth); these registrations are for menu
    # visibility + gateway command recognition. Non-fatal if unsupported.
    register_command = getattr(ctx, "register_command", None)
    if callable(register_command):
        for _name, _desc in _MENU_COMMANDS:
            try:
                register_command(name=_name, handler=_menu_command_fallback, description=_desc)
            except Exception as e:  # noqa: BLE001
                logger.warning("telegram-context register_command %s failed: %s", _name, e)
