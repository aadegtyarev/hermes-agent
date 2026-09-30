"""Telegram context tools — read the ingested message store."""
from __future__ import annotations

import time

from tools.registry import tool_error, tool_result

from . import store


def _fmt(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({
            "message_id": r.get("message_id"),
            "from": r.get("user_name") or r.get("user_id"),
            "reply_to": r.get("reply_to_message_id") or None,
            "reply_to_author": r.get("reply_to_author") or None,
            "text": (r.get("text") or "")[:2000],
        })
    return out


def _chat(args) -> str | None:
    return str(args.get("chat_id") or "").strip() or store.latest_chat()


def _since_until(args) -> tuple[float | None, float | None]:
    """Parse optional 'since_hours_ago'/'until_hours_ago' into epoch bounds.

    Hours-ago (not raw timestamps) because that's what an agent building a
    report actually reasons in ("last week" -> since_hours_ago=168) — no
    date-arithmetic footgun for the model to get wrong.
    """
    now = time.time()
    since = args.get("since_hours_ago")
    until = args.get("until_hours_ago")
    since_ts = now - float(since) * 3600 if since is not None else None
    until_ts = now - float(until) * 3600 if until is not None else None
    return since_ts, until_ts


_WINDOW_PARAMS = {
    "since_hours_ago": {"type": "number", "description": "Only messages from at most this many hours ago (e.g. 168 for the last week)."},
    "until_hours_ago": {"type": "number", "description": "Only messages from at least this many hours ago (default: now)."},
    "cursor": {"type": "integer", "description": "Resume pagination after this value (from a prior call's next_cursor). Pass 0 to start walking the window from its oldest message — omit entirely for the old 'most recent N' behavior."},
    "count_only": {"type": "boolean", "description": "Report total matching messages / time span / approx size only, no content — check this before paging a large window (e.g. building a report over weeks/months)."},
}


TELEGRAM_THREAD = {"name": "telegram_thread", "description": (
    "Reconstruct a Telegram thread from stored messages: the reply chain up to the root plus all "
    "replies below, in chronological order, with who replied to whom."),
    "parameters": {"type": "object", "properties": {
        "message_id": {"type": "string", "description": "A message id within the thread."},
        "chat_id": {"type": "string", "description": "Chat id (default: the most recently active chat)."}},
        "required": ["message_id"]}}

TELEGRAM_RECENT = {"name": "telegram_recent", "description": (
    "Recent messages in a chat, chronological (default: current/latest chat, "
    "last N messages). Pass since_hours_ago/until_hours_ago + cursor to instead "
    "walk an entire time window page by page — e.g. for a topic report/digest "
    "over weeks or months, too much to fit in one call. Check count_only first "
    "to see how much there is before paging."),
    "parameters": {"type": "object", "properties": {
        "chat_id": {"type": "string", "description": "Chat id (default: latest active chat)."},
        "limit": {"type": "integer", "description": "Max messages per call (default 50, max 500)."},
        **_WINDOW_PARAMS}, "required": []}}

TELEGRAM_SEARCH = {"name": "telegram_search", "description": (
    "Text search across stored Telegram messages (optionally within one chat), "
    "most recent matches by default. Pass since_hours_ago/until_hours_ago + "
    "cursor to instead walk every match in a time window exhaustively — check "
    "count_only first to see how many matches there are before paging."),
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "Substring to search for."},
        "chat_id": {"type": "string", "description": "Restrict to a chat (default: all chats)."},
        "limit": {"type": "integer", "description": "Max results per call (default 50, max 500)."},
        **_WINDOW_PARAMS}, "required": ["query"]}}

TELEGRAM_DM_ALLOWLIST = {"name": "telegram_dm_allowlist", "description": "List users auto-collected into the DM allowlist (from work chats).",
    "parameters": {"type": "object", "properties": {}, "required": []}}

ESCALATE_TO_TEAM_SCHEMA = {"name": "escalate_to_team", "description": (
    "Bring in the human team responsible for this chat — posts to their own team "
    "chat, never a private DM. Think of it like a capable junior colleague calling "
    "over a senior: normal and expected when you're genuinely not confident in your "
    "own answer, or the other person explicitly asks to talk to someone more "
    "experienced. That is a good, professional call, not a failure — don't hold off "
    "just because you already tried to help. Only usable inside an isolated client "
    "chat; write 'message' the way you'd actually describe the situation to a "
    "teammate walking in cold, not a fill-in-the-blanks template. Rate-limited per "
    "chat — a repeat call too soon is reported back instead of sent again, so keep "
    "helping conversationally until it clears."),
    "parameters": {"type": "object", "properties": {
        "message": {"type": "string", "description": "What's going on and why you're bringing the team in, in your own words."},
        "message_id": {"type": "string", "description": "The specific message id this is about, if there is one clearly relevant — included as a link. Omit if nothing specific applies."},
    }, "required": ["message"]}}

_ESCALATION_COOLDOWN_SECONDS = 20 * 60


def handle_telegram_thread(args, **kw):
    mid = str(args.get("message_id") or "").strip()
    if not mid:
        return tool_error("telegram_thread needs 'message_id' (a message in the thread). Use telegram_recent to find message ids.")
    chat = _chat(args)
    if not chat:
        return tool_error("No chat to read (store is empty, or pass 'chat_id'). Messages accumulate only after the bot has seen them.")
    rows = store.thread(chat, mid)
    if not rows:
        return tool_error(f"No stored message '{mid}' in chat {chat}. It may predate ingest, or be in another chat — pass 'chat_id', or use telegram_recent/telegram_search.")
    return tool_result({"chat_id": chat, "count": len(rows), "thread": _fmt(rows)})


def handle_telegram_recent(args, **kw):
    chat = _chat(args)
    if not chat:
        return tool_error("No chat to read (store empty). Pass 'chat_id' or wait for messages.")
    since_ts, until_ts = _since_until(args)

    if args.get("count_only"):
        return tool_result({"chat_id": chat, **store.count(chat, since_ts, until_ts)})

    try:
        limit = int(args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    cursor = args.get("cursor")
    after_id = int(cursor) if cursor is not None else None

    page = store.recent(chat, min(limit, 500), since_ts, until_ts, after_id)
    return tool_result({
        "chat_id": chat, "count": len(page["messages"]), "messages": _fmt(page["messages"]),
        "has_more": page["has_more"], "next_cursor": page["next_cursor"],
    })


def handle_telegram_search(args, **kw):
    q = str(args.get("query") or "").strip()
    if not q:
        return tool_error("telegram_search needs 'query' (a substring). Example: telegram_search(query='CRC error').")
    chat_id = str(args.get("chat_id") or "").strip() or None
    since_ts, until_ts = _since_until(args)

    if args.get("count_only"):
        return tool_result({"query": q, **store.count(chat_id, since_ts, until_ts, query=q)})

    try:
        limit = int(args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    cursor = args.get("cursor")
    after_id = int(cursor) if cursor is not None else None

    page = store.search(q, chat_id, min(limit, 500), since_ts, until_ts, after_id)
    return tool_result({
        "query": q, "count": len(page["messages"]), "matches": _fmt(page["messages"]),
        "has_more": page["has_more"], "next_cursor": page["next_cursor"],
    })


def handle_telegram_dm_allowlist(args, **kw):
    users = store.dm_allowed_list()
    return tool_result({"count": len(users), "users": users})


def handle_escalate_to_team(args, **kw):
    # Lazy import: avoids a circular import at module-load time (__init__.py
    # imports this module at its own top level).
    from . import _md2_escape, _md2_link, _send, _telegram_chat_deep_link

    session_id = str(kw.get("session_id") or "")
    chat_id = store.origin_chat_id(session_id)
    if not chat_id or store.chat_mode(chat_id) != "client":
        return tool_error("escalate_to_team only works from inside an isolated client chat.")

    program = store.chat_program(chat_id)
    team_chat = store.program_chat_id(program) if program else None
    if not team_chat:
        return tool_error(f"Chat is registered under program {program!r}, which has no team chat on record.")

    message = str(args.get("message") or "").strip()
    if not message:
        return tool_error("Pass 'message' — describe the situation in your own words.")

    now = time.time()
    last = store.last_escalation_ts(chat_id)
    if last is not None and now - last < _ESCALATION_COOLDOWN_SECONDS:
        wait = int(_ESCALATION_COOLDOWN_SECONDS - (now - last))
        return tool_result({
            "escalated": False, "reason": "cooldown", "retry_after_seconds": wait,
            "hint": "Already escalated recently for this chat — keep helping conversationally instead of calling again.",
        })

    title = store.chat_title(chat_id) or chat_id
    msg_id = str(args.get("message_id") or "").strip() or None
    link = _telegram_chat_deep_link(chat_id, msg_id)
    chat_ref = _md2_link(title, link)
    text = f"Тебя зовут в чат {chat_ref}\\. {_md2_escape(message)}"
    _send(team_chat, text, parse_mode="MarkdownV2")
    store.record_escalation(chat_id, now)
    return tool_result({"escalated": True, "team_chat": team_chat})


TOOLS = (
    ("telegram_thread", TELEGRAM_THREAD, handle_telegram_thread, "🧵"),
    ("telegram_recent", TELEGRAM_RECENT, handle_telegram_recent, "🕘"),
    ("telegram_search", TELEGRAM_SEARCH, handle_telegram_search, "🔎"),
    ("telegram_dm_allowlist", TELEGRAM_DM_ALLOWLIST, handle_telegram_dm_allowlist, "👥"),
    ("escalate_to_team", ESCALATE_TO_TEAM_SCHEMA, handle_escalate_to_team, "🆘"),
)
