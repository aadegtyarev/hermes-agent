"""Telegram context tools — read the ingested message store."""
from __future__ import annotations

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


TELEGRAM_THREAD = {"name": "telegram_thread", "description": (
    "Reconstruct a Telegram thread from stored messages: the reply chain up to the root plus all "
    "replies below, in chronological order, with who replied to whom."),
    "parameters": {"type": "object", "properties": {
        "message_id": {"type": "string", "description": "A message id within the thread."},
        "chat_id": {"type": "string", "description": "Chat id (default: the most recently active chat)."}},
        "required": ["message_id"]}}

TELEGRAM_RECENT = {"name": "telegram_recent", "description": "Recent messages in a chat, chronological (default: current/latest chat).",
    "parameters": {"type": "object", "properties": {
        "chat_id": {"type": "string", "description": "Chat id (default: latest active chat)."},
        "limit": {"type": "integer", "description": "Max messages (default 50)."}},
        "required": []}}

TELEGRAM_SEARCH = {"name": "telegram_search", "description": "Text search across stored Telegram messages (optionally within one chat).",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "Substring to search for."},
        "chat_id": {"type": "string", "description": "Restrict to a chat (default: all chats)."},
        "limit": {"type": "integer", "description": "Max results (default 50)."}},
        "required": ["query"]}}

TELEGRAM_DM_ALLOWLIST = {"name": "telegram_dm_allowlist", "description": "List users auto-collected into the DM allowlist (from work chats).",
    "parameters": {"type": "object", "properties": {}, "required": []}}


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
    try:
        limit = int(args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    rows = store.recent(chat, min(limit, 500))
    return tool_result({"chat_id": chat, "count": len(rows), "messages": _fmt(rows)})


def handle_telegram_search(args, **kw):
    q = str(args.get("query") or "").strip()
    if not q:
        return tool_error("telegram_search needs 'query' (a substring). Example: telegram_search(query='CRC error').")
    try:
        limit = int(args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    rows = store.search(q, str(args.get("chat_id") or "").strip() or None, min(limit, 500))
    return tool_result({"query": q, "count": len(rows), "matches": _fmt(rows)})


def handle_telegram_dm_allowlist(args, **kw):
    users = store.dm_allowed_list()
    return tool_result({"count": len(users), "users": users})


TOOLS = (
    ("telegram_thread", TELEGRAM_THREAD, handle_telegram_thread, "🧵"),
    ("telegram_recent", TELEGRAM_RECENT, handle_telegram_recent, "🕘"),
    ("telegram_search", TELEGRAM_SEARCH, handle_telegram_search, "🔎"),
    ("telegram_dm_allowlist", TELEGRAM_DM_ALLOWLIST, handle_telegram_dm_allowlist, "👥"),
)
