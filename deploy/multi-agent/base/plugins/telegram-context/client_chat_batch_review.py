#!/usr/bin/env python3
"""Pre-check script for the client-chat batch-review cron job.

Registered as a script= (agent-invoking) hermes cron job by telegram-context's
register() (see _ensure_batch_review_cron_job in __init__.py) — copied to
$HERMES_HOME/scripts/ at plugin load time, same as telegram_db_backup.py.

Runs OFTEN (base tick every few minutes, see BASE_TICK note in __init__.py)
and does the cheap, no-LLM filtering pass: for every client-mode chat, decide
whether it is actually DUE for review right now, and only print something
when at least one chat is. cron/scheduler.py's own _build_job_prompt treats
empty script stdout as "nothing to report, skip the AI call entirely" — so a
quiet tick (the overwhelming majority) costs one sqlite read, no LLM call at
all. This is the real anti-spam mechanism the whole batch-review feature
rests on, not anything in the agent's own prompt.

"Due" combines two signals per chat:
  1. Adaptive interval by recent activity (see _tier_interval_seconds): a
     busy chat is checked less often (if a human is actively chatting there,
     it doesn't need LLM attention every few minutes; when it IS checked,
     there's simply a bigger batch to review in one pass instead of many
     small ones) — a quiet chat is checked often (cheap, since there's
     usually nothing new, and if something DOES break the silence it's more
     likely to matter and deserves fast attention).
  2. The chat's last message must be from the PARTNER side, not the team's
     own operator or the bot itself — checked live via getChatMember against
     the chat's program's own team chat (same status set as
     __init__.py's _user_is_member_of_chat; duplicated here rather than
     imported because this runs as a standalone subprocess, same reasoning
     as telegram_db_backup.py not importing store.py). An operator's own
     reply does NOT permanently exempt a chat — it only lowers this tick's
     urgency; the interval naturally brings it back up for review later so
     the model can judge whether that reply actually resolved things.

Works directly against telegram.db's raw schema via sqlite3 (not store.py)
because this runs as its own subprocess — same reasoning as
telegram_db_backup.py already established for this plugin's other cron script.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request

from hermes_constants import get_hermes_home

_MEMBER_STATUSES = {"creator", "administrator", "member", "restricted"}

# (max recent-messages-per-hour, review interval seconds) — first tier whose
# threshold the chat's recent rate is <= wins. Deliberately conservative
# starting points per the "start simple, observe, tune" approach used
# throughout this feature; tune via config once real traffic is observed.
_ACTIVITY_TIERS = (
    (1, 5 * 60),       # quiet: <=1 msg/hour -> check every 5 min
    (10, 20 * 60),      # normal: <=10 msg/hour -> check every 20 min
    (float("inf"), 60 * 60),  # busy: anything above -> check hourly
)

_MAX_MESSAGES_PER_CHAT = 30
_MAX_CHATS_PER_TICK = 25


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(get_hermes_home() / "telegram.db"), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE IF NOT EXISTS chat_review_state("
        "chat_id TEXT PRIMARY KEY, last_reviewed_ts REAL)"
    )
    return c


def _tier_interval_seconds(recent_hourly_rate: float) -> int:
    for max_rate, interval in _ACTIVITY_TIERS:
        if recent_hourly_rate <= max_rate:
            return interval
    return _ACTIVITY_TIERS[-1][1]


def _is_live_member(chat_id: str, user_id: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not chat_id or not user_id:
        return False
    try:
        url = (f"https://api.telegram.org/bot{token}/getChatMember"
               f"?chat_id={urllib.parse.quote(str(chat_id))}&user_id={user_id}")
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read().decode())
        return (data.get("result") or {}).get("status") in _MEMBER_STATUSES
    except Exception:
        return False


def _due_chats(c: sqlite3.Connection, now: float) -> list[dict]:
    client_chats = c.execute(
        "SELECT chat_id, title, program FROM chats_allowed WHERE mode='client'"
    ).fetchall()

    due = []
    for row in client_chats:
        chat_id, title, program = row["chat_id"], row["title"], row["program"]
        if not program:
            continue
        team_chat = c.execute(
            "SELECT chat_id FROM partner_programs WHERE program=?", (program,)
        ).fetchone()
        if not team_chat:
            continue

        last_reviewed = c.execute(
            "SELECT last_reviewed_ts FROM chat_review_state WHERE chat_id=?", (chat_id,)
        ).fetchone()
        last_reviewed_ts = last_reviewed["last_reviewed_ts"] if last_reviewed else 0.0

        hour_ago = now - 3600
        recent_count = c.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE chat_id=? AND ts>=?",
            (chat_id, hour_ago),
        ).fetchone()["n"]
        interval = _tier_interval_seconds(recent_count)
        if now - last_reviewed_ts < interval:
            continue  # not due yet at this chat's own activity-adjusted pace

        last_msg = c.execute(
            "SELECT user_id, user_name, text, ts FROM messages WHERE chat_id=? "
            "ORDER BY rowid DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        if not last_msg:
            continue  # nothing ever ingested here
        if _is_live_member(team_chat["chat_id"], last_msg["user_id"]):
            # Last word was already the team's own — not silent from the
            # partner's side. Still worth an occasional pass (the interval
            # check above already rate-limits this), but not urgent right now.
            continue

        new_messages = c.execute(
            "SELECT user_name, text, ts FROM messages WHERE chat_id=? AND ts>? "
            "ORDER BY rowid ASC LIMIT ?",
            (chat_id, last_reviewed_ts, _MAX_MESSAGES_PER_CHAT),
        ).fetchall()
        if not new_messages:
            continue

        due.append({
            "chat_id": chat_id,
            "title": title or chat_id,
            "program": program,
            "messages": [dict(m) for m in new_messages],
        })
        if len(due) >= _MAX_CHATS_PER_TICK:
            break

    return due


def main() -> int:
    now = time.time()
    with _conn() as c:
        due = _due_chats(c, now)
        if not due:
            return 0  # empty stdout -> scheduler skips the LLM call entirely

        for chat in due:
            c.execute(
                "INSERT OR REPLACE INTO chat_review_state(chat_id, last_reviewed_ts) VALUES(?,?)",
                (chat["chat_id"], now),
            )

    lines = []
    for chat in due:
        lines.append(f"### Chat: {chat['title']} (chat_id={chat['chat_id']}, program={chat['program']})")
        for m in chat["messages"]:
            lines.append(f"- {m['user_name'] or 'unknown'}: {m['text']}")
        lines.append("")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
