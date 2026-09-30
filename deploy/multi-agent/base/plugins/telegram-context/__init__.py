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

A separate `pre_tool_call` hook (`_pre_tool_call`) hard-blocks `send_message`
tool calls whose target resolves to a read-only chat — the actual "never
writes there" guarantee, independent of dispatch gating above (which only
ever governs whether the agent gets a *turn* for an inbound message; it says
nothing about the agent later, possibly prompt-injected from an entirely
different conversation, choosing to `send_message` there explicitly). An
earlier version tried to get this guarantee by mirroring this plugin's
readonly set into the core adapter's own `read_only_chats` field (a
send-level guard hermes-agent core offers for static `config.yaml` use) —
that backfired: the core field ALSO gates dispatch, so once mirrored, read-only
chats silently stopped reaching this plugin's `_on_dispatch` at all, including
admin commands like `/hermes_forget` typed from inside that chat. Blocking at
the tool-call boundary instead needs no core changes at all and leaves
dispatch routing untouched — see `_pre_tool_call`'s docstring.

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
  /hermes_forget    → drop this chat from the runtime list (or a program's team chat)
  /hermes_chats     → show the runtime list
Commands are handled in the hook (before gating, so they work in a not-yet-enrolled
chat), acknowledged via Bot API, and never forwarded to the agent. Commands reach the
bot even with group privacy mode on (`/cmd@Bot`); full ingest still needs privacy off.

A third chat mode, "client", generalizes beyond the journalist/partner use case
this was built for (tech support/customer, or any "isolated one-on-one chat per
outside party" pattern): dispatched like a work chat (same require_mention
gating decides whether a turn fires — no extra anti-spam logic needed), but
deliberately never calls add_dm_user()/auto-approves pairing, so membership in
a client chat can never grant DM access. Registration is ``/hermes_program
[name]`` — ONE command, contextual (see `_handle_hermes_program`):
  - run in a fresh chat by a global admin, with a NEW name → creates a
    "program" (e.g. "journalist-partners", "support-clients") bound to THIS
    chat as that program's own team chat.
  - run in a fresh chat, with an EXISTING program name (or auto-inferred from
    the caller's own program membership when omitted and unambiguous) →
    registers THIS chat as a client chat under that program. Open to anyone
    who is a LIVE member of the program's team chat right now — no separate
    admin list, mirroring the work-chat DM-auto-collection trust pattern.
Cross-chat isolation for client-mode sessions (telegram_search/telegram_recent
pinned to their own chat_id, session_search blocked, send_message restricted
to their own chat) lives in `_pre_tool_call`, same mechanism as the read-only
send-guard — see its docstring.

For auto-collection to work, leave the gateway's own TELEGRAM_ALLOWED_USERS empty
(this hook is the gate). Opt-in via plugins.enabled: [telegram-context] + toolset `telegram`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from hermes_constants import get_hermes_home

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


def _client_chats() -> set[str]:
    """Isolated client/partner chats — dynamic-only, no static env list.

    Unlike work/readonly, a client chat is only ever created through
    ``/hermes_program`` (registration requires live membership in some
    program's team chat), never a static ``TELEGRAM_*_CHATS`` env var.
    """
    return _store_chats("client")


def _admin_users() -> set[str]:
    return _csv("TELEGRAM_ADMIN_USERS")


def _user_is_member_of_chat(uid: str, chat_id: str) -> bool:
    """Live ``getChatMember`` check against one specific chat (not the work-chat loop).

    Backs program trust: membership in a program's own team chat IS the
    operator signal for that program (who can register client chats under
    it, who counts as "an operator replied" for the batch reviewer) — no
    separately-maintained admin list, mirroring how work-chat membership
    already grants DM access elsewhere in this plugin.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not uid or not chat_id:
        return False
    try:
        url = (f"https://api.telegram.org/bot{token}/getChatMember"
               f"?chat_id={urllib.parse.quote(str(chat_id))}&user_id={uid}")
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read().decode())
        return (data.get("result") or {}).get("status") in _MEMBER_STATUSES
    except Exception:
        return False


def _programs_for_user(uid: str) -> list[str]:
    """Which registered programs' team chats ``uid`` is currently a live member of."""
    return [p for p, chat in store.programs().items() if _user_is_member_of_chat(uid, chat)]


def _send(chat_id: str, text: str, parse_mode: str | None = None) -> None:
    """Send a reply via the Bot API (used to ack admin commands + escalations).

    ``parse_mode="MarkdownV2"`` lets a caller compose a masked link
    (``[title](url)``, see ``_md2_link``) — plain by default so every
    existing ack call is unaffected.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not chat_id:
        return
    try:
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        data = urllib.parse.urlencode(payload).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=8)
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram-context sendMessage failed: %s", e)


_MD2_SPECIAL_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def _md2_escape(text: str) -> str:
    """Escape MarkdownV2 special characters in plain text (Telegram Bot API)."""
    return _MD2_SPECIAL_RE.sub(r"\\\1", text or "")


def _md2_link(display: str, url: str | None) -> str:
    """A MarkdownV2 masked link (display text hides the URL), or just the
    escaped display text when no URL is available (e.g. a chat we can't
    build a deep link for)."""
    safe_display = _md2_escape(display or "")
    if not url:
        return safe_display
    # Only the closing paren and backslash need escaping inside a link URL
    # (MarkdownV2 spec) — not the general text-escape set above.
    safe_url = url.replace("\\", "\\\\").replace(")", "\\)")
    return f"[{safe_display}]({safe_url})"


def _telegram_chat_deep_link(chat_id: str, message_id: str | None = None) -> str | None:
    """Best-effort ``https://t.me/c/<id>/<message_id>`` deep link for a private
    supergroup/channel (the ``-100<id>`` numeric form — everything this plugin
    deals with). Returns None for shapes this can't build a link for (basic
    group chats have no stable public link at all); message_id defaults to 1
    as a generic "open the chat" anchor when no specific message applies."""
    cid = str(chat_id or "").strip()
    if not cid.startswith("-100"):
        return None
    internal_id = cid[4:]
    if not internal_id.isdigit():
        return None
    return f"https://t.me/c/{internal_id}/{message_id or 1}"


_CHAT_COMMANDS = {"/hermes_here", "/hermes_readonly", "/hermes_forget", "/hermes_chats", "/hermes_program"}
# Gated on global TELEGRAM_ADMIN_USERS. /hermes_program is NOT in this set —
# it has its own contextual auth (see _handle_hermes_program): creating a new
# program still needs a global admin, but registering a client chat under an
# EXISTING program only needs live membership in that program's own team chat.
_GLOBAL_ADMIN_COMMANDS = {"/hermes_here", "/hermes_readonly", "/hermes_forget", "/hermes_chats"}

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
    ("hermes_program", "Создать программу / зарегистрировать клиентский чат"),
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


_HANDLED = {"action": "skip", "reason": "telegram chat command handled"}
_NON_ADMIN_SILENT = {"action": "skip", "reason": "telegram chat command from non-admin"}


def _handle_hermes_program(chat_id: str, uid: str, title: str, raw_text: str):
    """Contextual ``/hermes_program [name]`` — one command, two jobs:

    - ``<name>`` doesn't exist yet -> CREATE a new program bound to the
      CURRENT chat as its team chat. Global-admin only (a new program is a
      new trust root — rare, deliberate bootstrap).
    - ``<name>`` already exists -> register the CURRENT chat as a CLIENT
      chat under that program. Open to anyone who is a LIVE member of that
      program's own team chat right now (no separate admin list to
      maintain — membership in the team chat IS the authorization, same
      pattern as work-chat DM auto-collection elsewhere in this plugin).
    - no ``<name>`` given -> infer from the caller's own program membership:
      exactly one -> register under it; several -> ask to disambiguate;
      none (and not a global admin) -> silent no-op, same as any other
      non-admin command misuse — never reveal the mechanism exists.

    Replies (and this function at all) only ever fire inside a chat that is
    either about to become registered or already is one of ours — never
    leaking usage help into an unrelated chat.
    """
    if store.chat_mode(chat_id) or store.is_program_team_chat(chat_id):
        _send(chat_id, "Этот чат уже зарегистрирован.")
        return _HANDLED

    parts = raw_text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    progs = store.programs()

    def _register_client(program_name: str) -> None:
        store.set_chat(chat_id, "client", title, uid, program=program_name)
        _send(chat_id, f"✅ Чат зарегистрирован как клиентский под программой «{program_name}».")
        team_chat = progs.get(program_name) or store.program_chat_id(program_name)
        _send(team_chat, f"➕ Добавлен новый клиентский чат под «{program_name}»: "
              f"{title or chat_id} (добавил uid={uid}).")

    if arg:
        if arg in progs:
            if not _user_is_member_of_chat(uid, progs[arg]):
                return _NON_ADMIN_SILENT
            _register_client(arg)
            return _HANDLED
        if uid not in _admin_users():
            return _NON_ADMIN_SILENT
        ok = store.create_program(arg, chat_id, uid)
        _send(chat_id, f"✅ Программа «{arg}» создана — этот чат теперь её команда." if ok
              else f"Не удалось создать «{arg}»: имя занято, или этот чат уже зарегистрирован.")
        return _HANDLED

    mine = _programs_for_user(uid)
    if len(mine) == 1:
        _register_client(mine[0])
    elif len(mine) >= 2:
        _send(chat_id, "Вы состоите в нескольких программах — укажите явно: /hermes_program <name>")
    elif uid in _admin_users():
        _send(chat_id, "Укажите имя новой программы: /hermes_program <name>")
    else:
        return _NON_ADMIN_SILENT
    return _HANDLED


def _handle_command(event, src, chat_id: str, uid: str):
    """If the message is a /hermes_* chat-admin command, act on it and return a skip
    action (so it isn't forwarded to the agent). Returns None if not a command."""
    text = (getattr(event, "text", "") or "").strip()
    if not text.startswith("/hermes_"):
        return None
    cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()  # strip @BotUsername
    if cmd not in _CHAT_COMMANDS:
        return None
    title = getattr(src, "chat_name", "") or ""

    if cmd == "/hermes_program":
        return _handle_hermes_program(chat_id, uid, title, text)

    if uid not in _admin_users():
        # Silent ignore, no "⛔ not allowed" reply — a non-admin poking
        # /hermes_* shouldn't get any acknowledgement that the command
        # exists or was noticed at all.
        return _NON_ADMIN_SILENT
    if cmd == "/hermes_here":
        store.set_chat(chat_id, "work", title, uid)
        _send(chat_id, "✅ Чат добавлен как рабочий — отвечаю здесь.")
    elif cmd == "/hermes_readonly":
        store.set_chat(chat_id, "readonly", title, uid)
        _send(chat_id, "👀 Чат добавлен как read-only — читаю для контекста, не отвечаю.")
    elif cmd == "/hermes_forget":
        removed_program = store.remove_program_by_chat(chat_id)
        removed_chat = store.remove_chat(chat_id)
        if removed_program:
            _send(chat_id, f"🗑 Программа «{removed_program}» удалена (чат команды освобождён).")
        elif removed_chat:
            _send(chat_id, "🗑 Чат убран из списка.")
        else:
            _send(chat_id, "Этого чата нет в динамическом списке (возможно, он задан через .env).")
    elif cmd == "/hermes_chats":
        rows = store.list_chats()
        progs = store.programs()
        lines = [f"• {r['mode']}: {r['chat_id']}"
                 + (f" [{r['program']}]" if r.get("program") else "")
                 + (f" — {r['title']}" if r.get("title") else "")
                 for r in rows]
        lines += [f"• program «{name}»: team chat {chat}" for name, chat in progs.items()]
        if lines:
            _send(chat_id, "Динамический список:\n" + "\n".join(lines))
        else:
            _send(chat_id, "Динамический список пуст (чаты также могут быть заданы через .env).")
    return _HANDLED


_GDOC_LINK_RE = re.compile(r"https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)")


def _link_chat_docs(chat_id: str, text: str) -> None:
    """Record any Google Docs links seen in a chat's own message text.

    The ONLY source of truth for "which documents may gdoc_read touch from
    this chat" (see the isolation guard in _pre_tool_call) — populated
    incrementally as links are actually shared, never pre-registered at
    chat-registration time (a client chat may share several documents over
    its lifetime; which ones matter isn't known up front). Cheap and
    unconditional: runs for every ingested message regardless of chat mode,
    since the data only matters when read back for a client-mode chat.
    """
    if not chat_id or not text:
        return
    for m in _GDOC_LINK_RE.finditer(text):
        store.link_chat_doc(chat_id, m.group(1))


def _ingest(event) -> None:
    src = getattr(event, "source", None)
    if src is None:
        return
    chat_id = str(getattr(src, "chat_id", "") or "")
    text = getattr(event, "text", "") or ""
    store.add({
        "chat_id": chat_id,
        "message_id": str(getattr(event, "message_id", "") or ""),
        "ts": time.time(),
        "user_id": str(getattr(src, "user_id", "") or ""),
        "user_name": getattr(src, "user_name", "") or "",
        "chat_type": getattr(src, "chat_type", "") or "",
        "chat_name": getattr(src, "chat_name", "") or "",
        "thread_id": str(getattr(src, "thread_id", "") or ""),
        "text": text,
        "reply_to_message_id": str(getattr(event, "reply_to_message_id", "") or ""),
        "reply_to_author": getattr(event, "reply_to_author_name", "") or "",
    })
    _link_chat_docs(chat_id, text)


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


_TELEGRAM_TARGET_CHAT_RE = re.compile(r"^\s*telegram(?::(-?\d+))?(?::\d+)?\s*$", re.IGNORECASE)

# Tools whose cross-chat reach must be cut off for a client-mode chat's own
# session — see _pre_tool_call's docstring for why each one is here.
_CROSS_CHAT_READ_TOOLS = {"telegram_search", "telegram_recent"}

# Mirrors google-docs/tools.py's own _DOC_ID_RE — duplicated rather than
# imported so this plugin's isolation guard has no hard dependency on the
# google-docs plugin being installed/enabled, same precedent as the
# send_message target regex above not importing tools/send_message_tool.py.
_GDOC_ID_FROM_URL_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")


def _parse_gdoc_id(ref: str) -> str:
    ref = (ref or "").strip()
    m = _GDOC_ID_FROM_URL_RE.search(ref)
    if m:
        return m.group(1)
    if ref and "/" not in ref and " " not in ref:
        return ref
    return ""


def _pre_tool_call(tool_name=None, args=None, **kwargs):
    """Two independent hard guarantees, both keyed on chat mode, not on prompting:

    1. ``send_message`` aimed at a read-only chat is blocked outright — the
       actual "never writes there, no matter what" guarantee. ``pre_gateway_
       dispatch`` (the gate in ``_on_dispatch`` below) only ever stops the
       agent from getting a *turn* for a message that arrived FROM a
       read-only chat; it says nothing about the agent later choosing (or
       being prompt-injected into choosing, from an entirely different
       conversation) to explicitly ``send_message`` there. ``send_message``'s
       ``target`` is a documented, unambiguous ``"platform:chat_id[:thread_id]"``
       string (tools/send_message_tool.py) — cheap and reliable to match here
       without needing to touch core or duplicate the tool's own resolution
       logic.

       An earlier version tried to get this guarantee by mirroring this
       plugin's readonly set into the core adapter's ``read_only_chats`` (a
       send-level guard hermes-agent core offers for static ``config.yaml``
       use) — that backfired: the core field ALSO gates dispatch, so once
       mirrored, read-only chats silently stopped reaching this plugin's
       ``_on_dispatch`` at all, including admin commands like
       ``/hermes_forget`` typed from inside that chat. Blocking at the
       tool-call boundary instead needs no core changes at all.

    2. A **client-mode chat's own session** may never reach outside itself:
       ``send_message`` may only target its OWN chat (the only legitimate way
       out is a dedicated escalation tool, landing in a follow-up PR — not
       ``send_message`` to an arbitrary chat_id); ``telegram_search``/
       ``telegram_recent`` may only be scoped to its own chat_id (omitting
       chat_id, or passing a different one, is blocked rather than silently
       narrowed — a silent rewrite would hide the restriction from the model
       instead of teaching it the right call); ``session_search`` is blocked
       outright (it's a cross-session discovery tool with no per-chat scoping
       at all — nothing a client-chat assistant legitimately needs). Work and
       read-only chats are completely unaffected — this whole guarantee is
       ONE-DIRECTIONAL: it isolates a client chat's own session from seeing
       anything else, it does not stop a work-chat session from reading a
       client chat's history (that cross-read is intentional — see the
       registry's module docstring).

       Resolving "which chat is this session in" needs ``store.origin_chat_id``
       (see its docstring) since chat_id isn't part of the hook's own
       kwargs — only checked for the specific tool names above, so every
       other tool call (the overwhelming majority) exits on the first line
       below with no lookup at all.
    """
    if tool_name == "send_message":
        target = str((args or {}).get("target") or "").strip()
        m = _TELEGRAM_TARGET_CHAT_RE.match(target)
        if not m:
            return None
        target_chat_id = m.group(1) or os.environ.get("TELEGRAM_HOME_CHANNEL", "").strip()
        if target_chat_id and target_chat_id in _readonly_chats():
            return {
                "action": "block",
                "message": f"Chat {target_chat_id} is read-only — this bot never writes there. "
                           "Not something to work around; pick a different target or drop the send.",
            }
        origin_chat = store.origin_chat_id(kwargs.get("session_id") or "")
        if origin_chat and store.chat_mode(origin_chat) == "client":
            if not target_chat_id or str(target_chat_id) != str(origin_chat):
                return {
                    "action": "block",
                    "message": "This is an isolated client chat — send_message may only target "
                               "this same chat. There is no cross-chat messaging from here.",
                }
        return None

    if tool_name in _CROSS_CHAT_READ_TOOLS:
        origin_chat = store.origin_chat_id(kwargs.get("session_id") or "")
        if origin_chat and store.chat_mode(origin_chat) == "client":
            requested = str((args or {}).get("chat_id") or "").strip()
            if requested != str(origin_chat):
                return {
                    "action": "block",
                    "message": f"This is an isolated client chat — {tool_name} only works scoped "
                               f"to its own history. Pass chat_id='{origin_chat}' explicitly.",
                }
        return None

    if tool_name == "session_search":
        origin_chat = store.origin_chat_id(kwargs.get("session_id") or "")
        if origin_chat and store.chat_mode(origin_chat) == "client":
            return {
                "action": "block",
                "message": "session_search isn't available in this chat — it's an isolated "
                           "client chat with no cross-session access.",
            }
        return None

    if tool_name == "gdrive_search":
        origin_chat = store.origin_chat_id(kwargs.get("session_id") or "")
        if origin_chat and store.chat_mode(origin_chat) == "client":
            return {
                "action": "block",
                "message": "gdrive_search isn't available in this chat — only documents already "
                           "shared here can be opened with gdoc_read; nothing can be searched for.",
            }
        return None

    if tool_name in ("gdoc_read", "gdoc_comments"):
        origin_chat = store.origin_chat_id(kwargs.get("session_id") or "")
        if origin_chat and store.chat_mode(origin_chat) == "client":
            doc_id = _parse_gdoc_id(str((args or {}).get("url") or ""))
            if not doc_id or doc_id not in store.chat_doc_ids(origin_chat):
                return {
                    "action": "block",
                    "message": "This is an isolated client chat — gdoc_read/gdoc_comments only "
                               "work for a document already shared in THIS chat's own history. "
                               "That document hasn't appeared here.",
                }
        return None

    return None


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

        work, ro, client = _work_chats(), _readonly_chats(), _client_chats()

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
        if chat_id in client:
            # Isolated client/partner chat: ingested and dispatched exactly
            # like a work chat (normal require_mention gating decides whether
            # the turn actually fires — no extra spam-avoidance logic needed
            # here), with ONE deliberate omission: no add_dm_user()/
            # _auto_approve_pairing(). Being in a client chat must never grant
            # DM access to the bot — this omission is the entire mechanism
            # that enforces that. Cross-chat data/tool isolation for this
            # mode is enforced separately in _pre_tool_call, not here.
            _ingest(event)
            return None
        if chat_id in ro:
            _ingest(event)
            return {"action": "skip", "reason": "read-only chat (observe only)"}
        if not work and not ro and not client:
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


_BACKUP_JOB_NAME = "telegram-db-nightly-backup"
_BACKUP_SCRIPT_FILENAME = "telegram_db_backup.py"


def _ensure_backup_cron_job() -> None:
    """Register the nightly telegram.db backup as a hermes cron job, once.

    hermes's cron scheduler resolves ``script=`` paths under
    ``$HERMES_HOME/scripts/`` (the writable data volume) — not under
    ``/opt/allowed/plugins`` (:ro bundled-plugins mount, where this file
    itself lives). So this copies the bundled script there on every plugin
    load (keeps it in sync with this plugin's version across redeploys —
    it's a system-managed file, never hand-edited) but only CREATES the cron
    job entry the first time, so a redeploy/restart never double-registers
    it or clobbers an operator's own edits to the job's schedule/enabled
    state. The job only ever runs while this gateway process is up, same as
    every other cron job — nothing extra needed for "only if the bot is
    running".
    """
    try:
        from cron import jobs as cron_jobs

        src = Path(__file__).resolve().parent / _BACKUP_SCRIPT_FILENAME
        dest = get_hermes_home() / "scripts" / _BACKUP_SCRIPT_FILENAME
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        if any(j.get("name") == _BACKUP_JOB_NAME for j in cron_jobs.list_jobs(include_disabled=True)):
            _dedupe_cron_jobs_by_name(cron_jobs, _BACKUP_JOB_NAME)
            return

        cron_jobs.create_job(
            prompt=None,
            schedule="0 3 * * *",
            name=_BACKUP_JOB_NAME,
            script=_BACKUP_SCRIPT_FILENAME,
            no_agent=True,
        )
        logger.info("telegram-context: registered nightly telegram.db backup cron job")
        _dedupe_cron_jobs_by_name(cron_jobs, _BACKUP_JOB_NAME)
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram-context: failed to register backup cron job: %s", e)


_BATCH_REVIEW_JOB_NAME = "client-chat-batch-review"
_BATCH_REVIEW_SCRIPT_FILENAME = "client_chat_batch_review.py"
_BATCH_REVIEW_TOOLSET = "telegram_batch_review"

_BATCH_REVIEW_PROMPT = (
    "Review the client chats listed in the script output above — each is a "
    "chat that's been quiet (no team reply) and just became due for a check. "
    "For any that genuinely need the team's attention (a real question, a "
    "file/photo worth a look, a problem — not routine chatter), call "
    "partner_flag_chats once with all of them. An empty/omitted call is "
    "completely normal when nothing qualifies. After that, respond with "
    "exactly \"[SILENT]\" — delivery already happened via the tool call, "
    "there is nothing else to report."
)


def _dedupe_cron_jobs_by_name(cron_jobs, name: str) -> None:
    """Keep only the oldest job named ``name``, removing any extras.

    Belt-and-suspenders against a TOCTOU race in the create-if-absent pattern
    both cron jobs in this plugin use: two near-simultaneous register() calls
    (observed in production — two plugin loads within 7ms of each other at
    container start both saw "not yet registered" before either create()
    call had persisted) can both pass the "does it exist?" check and both
    create a job. Harmless functionally (the second job's own due-chat
    filtering finds nothing new once the first has already run that tick),
    but wasteful — call this right after create_job() so a race this plugin
    load hit gets cleaned up on the NEXT load rather than accumulating.
    """
    try:
        jobs = sorted(
            (j for j in cron_jobs.list_jobs(include_disabled=True) if j.get("name") == name),
            key=lambda j: j.get("created_at") or "",
        )
        for extra in jobs[1:]:
            cron_jobs.remove_job(extra["id"])
            logger.warning(
                "telegram-context: removed duplicate cron job %r (id=%s) — "
                "a race created more than one", name, extra["id"],
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram-context: cron job dedupe check failed for %r: %s", name, e)


def _ensure_batch_review_cron_job() -> None:
    """Register the client-chat batch reviewer as a hermes cron job, once.

    Mirrors _ensure_backup_cron_job's copy-script-every-load /
    create-job-only-once pattern exactly. Unlike the backup job, this one
    DOES invoke the agent (no_agent=False) — but the script's own due-chat
    filtering (see client_chat_batch_review.py) means the overwhelming
    majority of ticks produce empty stdout, and cron/scheduler.py already
    skips the LLM call entirely for empty script output. enabled_toolsets
    scopes this job's agent to ONLY partner_flag_chats — it never gets
    telegram_search/send_message/etc., so it cannot do anything BUT flag
    chats from what the script handed it.
    """
    try:
        from cron import jobs as cron_jobs

        src = Path(__file__).resolve().parent / _BATCH_REVIEW_SCRIPT_FILENAME
        dest = get_hermes_home() / "scripts" / _BATCH_REVIEW_SCRIPT_FILENAME
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        if any(j.get("name") == _BATCH_REVIEW_JOB_NAME for j in cron_jobs.list_jobs(include_disabled=True)):
            _dedupe_cron_jobs_by_name(cron_jobs, _BATCH_REVIEW_JOB_NAME)
            return

        cron_jobs.create_job(
            prompt=_BATCH_REVIEW_PROMPT,
            schedule="every 5m",
            name=_BATCH_REVIEW_JOB_NAME,
            script=_BATCH_REVIEW_SCRIPT_FILENAME,
            enabled_toolsets=[_BATCH_REVIEW_TOOLSET],
            deliver="local",
        )
        logger.info("telegram-context: registered client-chat batch-review cron job")
        _dedupe_cron_jobs_by_name(cron_jobs, _BATCH_REVIEW_JOB_NAME)
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram-context: failed to register batch-review cron job: %s", e)


def register(ctx) -> None:
    try:
        store.init()
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram-context store init failed: %s", e)
    _ensure_backup_cron_job()
    _ensure_batch_review_cron_job()
    for name, schema, handler, emoji in T.TOOLS:
        ctx.register_tool(name=name, toolset="telegram", schema=schema, handler=handler, emoji=emoji)
    for name, schema, handler, emoji in T.BATCH_REVIEW_TOOLS:
        ctx.register_tool(name=name, toolset=_BATCH_REVIEW_TOOLSET, schema=schema, handler=handler, emoji=emoji)
    ctx.register_hook("pre_gateway_dispatch", _on_dispatch)
    ctx.register_hook("telegram_chat_member_left", _on_chat_member_left)
    ctx.register_hook("pre_tool_call", _pre_tool_call)
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
