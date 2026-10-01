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
    "over a senior: normal and expected when, AFTER genuinely trying to help, you're "
    "still not confident in your own answer and it needs the team's expertise or a "
    "decision, or the other person explicitly asks to talk to a human / someone more "
    "experienced. That is a good, professional call, not a failure. But it is NOT a "
    "reflex for 'I can't see / don't know something': do NOT escalate for a fact you "
    "could just look up (use web_search/web_extract first), or for a question about "
    "another chat or anything outside this chat that you simply don't have visibility "
    "into — for those, just say so plainly and offer what you can actually help with. "
    "Only usable inside an isolated client chat; write 'message' the way you'd "
    "actually describe the situation to a teammate walking in cold, not a "
    "fill-in-the-blanks template. Rate-limited per chat — a repeat call too soon is "
    "reported back instead of sent again, so keep helping conversationally until it "
    "clears."),
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


def _escalate_chat(chat_id: str, message: str, message_id: str | None = None) -> dict:
    """Shared core of escalate_to_team and the batch reviewer's flag path:
    resolve chat -> program -> team chat, apply the per-chat cooldown, and
    send the formatted notification. Returns a plain dict (not a JSON string)
    so callers can fold it into their own tool_result/tool_error shape.
    """
    # Lazy import: avoids a circular import at module-load time (__init__.py
    # imports this module at its own top level).
    from . import _md2_escape, _md2_link, _send, _telegram_chat_deep_link

    if not chat_id or store.chat_mode(chat_id) != "client":
        return {"ok": False, "error": f"{chat_id} is not a registered client chat."}

    program = store.chat_program(chat_id)
    team_chat = store.program_chat_id(program) if program else None
    if not team_chat:
        return {"ok": False, "error": f"Chat is registered under program {program!r}, which has no team chat on record."}

    message = (message or "").strip()
    if not message:
        return {"ok": False, "error": "No message given — describe the situation."}

    now = time.time()
    last = store.last_escalation_ts(chat_id)
    if last is not None and now - last < _ESCALATION_COOLDOWN_SECONDS:
        wait = int(_ESCALATION_COOLDOWN_SECONDS - (now - last))
        return {"ok": True, "escalated": False, "reason": "cooldown", "retry_after_seconds": wait}

    # message_id is model-supplied free text, interpolated into a MarkdownV2
    # URL (_telegram_chat_deep_link). _md2_link's own escaping keeps it from
    # changing the host, but a non-numeric value (stray space, newline) still
    # produces a malformed link URL -> a MarkdownV2 parse error from Telegram
    # -> _send below reports failure, same as any other delivery failure.
    # Validating here instead gives a clearer, specific error immediately.
    message_id = (message_id or "").strip()
    if message_id and not message_id.isdigit():
        message_id = None

    title = store.chat_title(chat_id) or chat_id
    link = _telegram_chat_deep_link(chat_id, message_id)
    chat_ref = _md2_link(title, link)
    text = f"Тебя зовут в чат {chat_ref}\\. {_md2_escape(message)}"
    if not _send(team_chat, text, parse_mode="MarkdownV2"):
        # Deliberately NOT recording the escalation/burning the cooldown on a
        # confirmed delivery failure — the whole point of checking _send's
        # return value instead of assuming success. The caller can retry.
        return {"ok": True, "escalated": False, "reason": "delivery_failed"}
    store.record_escalation(chat_id, now)
    return {"ok": True, "escalated": True, "team_chat": team_chat}


def handle_escalate_to_team(args, **kw):
    session_id = str(kw.get("session_id") or "")
    chat_id = store.origin_chat_id(session_id)
    if not chat_id:
        return tool_error("escalate_to_team only works from inside an isolated client chat.")

    message = str(args.get("message") or "").strip()
    msg_id = str(args.get("message_id") or "").strip() or None
    result = _escalate_chat(chat_id, message, msg_id)
    if not result.pop("ok"):
        return tool_error(result["error"])
    return tool_result(result)


# --------------------------------------------------------------------------- #
# partner_flag_chats — the batch reviewer's own tool. NOT in the `telegram`
# toolset a normal chat turn gets: registered into a separate
# `telegram_batch_review` toolset that only the batch-review cron job's
# agent is given (see _ensure_batch_review_cron_job's enabled_toolsets).
# --------------------------------------------------------------------------- #

PARTNER_FLAG_CHATS_SCHEMA = {"name": "partner_flag_chats", "description": (
    "Flag which of the reviewed client chats actually need the team's attention "
    "right now, with a short reason each. Only flag chats from the script output "
    "above — never invent a chat_id. Skip routine chatter ('спасибо', 'ок', small "
    "talk); flag a real question, a file/photo that needs a look, a problem, or "
    "anything a person would actually want to see. An empty list is a completely "
    "normal, expected result when nothing needs attention this pass."),
    "parameters": {"type": "object", "properties": {
        "flags": {"type": "array", "items": {"type": "object", "properties": {
            "chat_id": {"type": "string"},
            "reason": {"type": "string", "description": "What's going on, in your own words — becomes the team notification text."},
        }, "required": ["chat_id", "reason"]}},
    }, "required": ["flags"]}}


_RECENTLY_REVIEWED_WINDOW_SECONDS = 15 * 60  # generous vs. cron tick + agent-run time


def handle_partner_flag_chats(args, **kw):
    flags = args.get("flags")
    if not isinstance(flags, list):
        return tool_error("Pass 'flags' as a list of {chat_id, reason}.")

    results = []
    for entry in flags:
        if not isinstance(entry, dict):
            continue
        chat_id = str(entry.get("chat_id") or "").strip()
        reason = str(entry.get("reason") or "").strip()
        if not chat_id or not reason:
            results.append({"chat_id": chat_id, "ok": False, "error": "missing chat_id/reason"})
            continue
        # This tool's whole input (the chat digest) is partner-authored text,
        # injected into the batch-reviewer's own prompt — the model could be
        # steered into flagging a chat_id that was never actually part of
        # THIS tick's digest (an unrelated client chat under a DIFFERENT
        # program, say), which would deliver an attacker-chosen message into
        # that other program's team chat. chat_review_state.last_reviewed_ts
        # is set ONLY by client_chat_batch_review.py, for the chats it
        # actually included this run — a chat_id without a recent timestamp
        # there was not shown to the model this tick and is refused here,
        # independent of whether it's otherwise a validly-registered chat.
        reviewed = store.last_reviewed_ts(chat_id)
        if reviewed is None or time.time() - reviewed > _RECENTLY_REVIEWED_WINDOW_SECONDS:
            results.append({
                "chat_id": chat_id, "ok": False,
                "error": "this chat wasn't part of the current review batch",
            })
            continue
        outcome = _escalate_chat(chat_id, reason)
        results.append({"chat_id": chat_id, **outcome})

    return tool_result({"count": len(results), "results": results})


PROGRAM_CLIENT_CHATS_SCHEMA = {"name": "program_client_chats", "description": (
    "List the client chats of THIS program — only works from a program's own team "
    "chat (the chat where the team coordinates). Use it to work top-down: build a "
    "digest across all the program's client chats, or find a specific one by name "
    "or topic. Without 'query' it returns every client chat (chat_id, title, last "
    "activity, last-reviewed time, message count), newest-active first — then read "
    "each with telegram_recent(chat_id=…). With 'query' it returns only the client "
    "chats that match: by title, and by message content (full-text, so you can find "
    "'the chat where CAN bus came up'). Returns nothing outside a team chat; a "
    "client chat cannot use this to see sibling chats."),
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "Optional. Filter to client chats matching this in their title or message text. Omit to list all."},
        "since_hours_ago": {"type": "number", "description": "Optional. With 'query', only match messages from at most this many hours ago."},
        "limit": {"type": "integer", "description": "Max client chats to return (default 50)."},
    }, "required": []}}


def handle_program_client_chats(args, **kw):
    session_id = str(kw.get("session_id") or "")
    origin = store.origin_chat_id(session_id)
    program = store.program_by_team_chat(origin) if origin else None
    if not program:
        return tool_error(
            "program_client_chats only works from a program's own team chat. "
            "This session isn't in one."
        )
    limit = int((args or {}).get("limit") or 50)
    chats = store.program_client_chats(program)
    by_id = {c["chat_id"]: c for c in chats}

    query = str((args or {}).get("query") or "").strip()
    if not query:
        return tool_result({
            "program": program, "count": len(chats),
            "client_chats": [_fmt_client_chat(c) for c in chats[:limit]],
        })

    # Match on title (substring, case-insensitive) OR message content (FTS).
    ql = query.lower()
    matched: dict[str, dict] = {
        cid: dict(c, matched_on="title")
        for cid, c in by_id.items() if ql in (c.get("title") or "").lower()
    }
    since_ts, _ = _since_until(args)
    hits = store.search_in_chats(query, list(by_id.keys()), 500, since_ts)
    for h in hits:
        cid = h["chat_id"]
        base = by_id.get(cid)
        if base is None:
            continue
        if cid not in matched:
            matched[cid] = dict(base, matched_on="content")
        matched[cid].setdefault("snippet", (h.get("text") or "")[:300])

    results = sorted(matched.values(), key=lambda c: c.get("last_ts") or 0, reverse=True)
    return tool_result({
        "program": program, "query": query, "count": len(results),
        "client_chats": [_fmt_client_chat(c) for c in results[:limit]],
    })


def _fmt_client_chat(c: dict) -> dict:
    out = {
        "chat_id": c.get("chat_id"),
        "title": c.get("title"),
        "last_activity_ts": c.get("last_ts"),
        "last_reviewed_ts": c.get("last_reviewed_ts"),
        "message_count": c.get("msg_count"),
    }
    if c.get("matched_on"):
        out["matched_on"] = c["matched_on"]
    if c.get("snippet"):
        out["snippet"] = c["snippet"]
    return out


TOOLS = (
    ("telegram_thread", TELEGRAM_THREAD, handle_telegram_thread, "🧵"),
    ("telegram_recent", TELEGRAM_RECENT, handle_telegram_recent, "🕘"),
    ("telegram_search", TELEGRAM_SEARCH, handle_telegram_search, "🔎"),
    ("telegram_dm_allowlist", TELEGRAM_DM_ALLOWLIST, handle_telegram_dm_allowlist, "👥"),
    ("escalate_to_team", ESCALATE_TO_TEAM_SCHEMA, handle_escalate_to_team, "🆘"),
    ("program_client_chats", PROGRAM_CLIENT_CHATS_SCHEMA, handle_program_client_chats, "🗂️"),
)

# Registered into its own toolset (telegram_batch_review), NOT `telegram` —
# only the batch-review cron job's agent is granted that toolset. See
# __init__.py's register()/_ensure_batch_review_cron_job.
BATCH_REVIEW_TOOLS = (
    ("partner_flag_chats", PARTNER_FLAG_CHATS_SCHEMA, handle_partner_flag_chats, "🚩"),
)
