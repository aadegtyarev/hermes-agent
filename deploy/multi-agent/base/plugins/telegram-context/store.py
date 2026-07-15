"""Per-agent SQLite store of incoming Telegram messages ($HERMES_HOME/telegram.db).

The Bot API can't fetch history, so we persist messages as they arrive (via the
ingest hook) and reconstruct threads / recent / search from here. Isolated per
agent (its own data volume).
"""
from __future__ import annotations

import sqlite3
import threading

from hermes_constants import get_hermes_home

_LOCK = threading.Lock()


def _db_path() -> str:
    return str(get_hermes_home() / "telegram.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path(), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init() -> None:
    with _LOCK, _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS messages(
                chat_id TEXT, message_id TEXT, ts REAL,
                user_id TEXT, user_name TEXT, chat_type TEXT, chat_name TEXT,
                thread_id TEXT, text TEXT,
                reply_to_message_id TEXT, reply_to_author TEXT,
                PRIMARY KEY (chat_id, message_id))"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_chat_ts ON messages(chat_id, ts)")
        # Auto-collected DM allowlist: users seen in / confirmed members of work chats.
        c.execute(
            """CREATE TABLE IF NOT EXISTS dm_allowed(
                user_id TEXT PRIMARY KEY, user_name TEXT, source_chat TEXT, added_ts REAL)"""
        )
        # Runtime chat allowlist: chats enrolled via admin commands (no file edits).
        # mode ∈ {work, readonly}; unioned with the TELEGRAM_*_CHATS env at gate time.
        c.execute(
            """CREATE TABLE IF NOT EXISTS chats_allowed(
                chat_id TEXT PRIMARY KEY, mode TEXT, title TEXT, added_by TEXT, added_ts REAL)"""
        )


def set_chat(chat_id: str, mode: str, title: str = "", added_by: str = "") -> None:
    import time
    if not chat_id or mode not in ("work", "readonly"):
        return
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO chats_allowed(chat_id,mode,title,added_by,added_ts) VALUES(?,?,?,?,?)",
            (str(chat_id), mode, title, str(added_by), time.time()),
        )


def remove_chat(chat_id: str) -> bool:
    if not chat_id:
        return False
    with _LOCK, _conn() as c:
        return c.execute("DELETE FROM chats_allowed WHERE chat_id=?", (str(chat_id),)).rowcount > 0


def chats_by_mode(mode: str) -> set[str]:
    with _conn() as c:
        return {r["chat_id"] for r in
                c.execute("SELECT chat_id FROM chats_allowed WHERE mode=?", (mode,)).fetchall()}


def list_chats() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT chat_id,mode,title,added_by FROM chats_allowed ORDER BY added_ts DESC").fetchall()]


def add_dm_user(user_id: str, user_name: str = "", source_chat: str = "") -> None:
    import time
    if not user_id:
        return
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO dm_allowed(user_id,user_name,source_chat,added_ts) VALUES(?,?,?,?)",
            (str(user_id), user_name, source_chat, time.time()),
        )


def remove_dm_user(user_id: str) -> bool:
    if not user_id:
        return False
    with _LOCK, _conn() as c:
        return c.execute("DELETE FROM dm_allowed WHERE user_id=?", (str(user_id),)).rowcount > 0


def is_dm_allowed(user_id: str) -> bool:
    if not user_id:
        return False
    with _conn() as c:
        return c.execute("SELECT 1 FROM dm_allowed WHERE user_id=?", (str(user_id),)).fetchone() is not None


def dm_allowed_list() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT user_id,user_name,source_chat FROM dm_allowed ORDER BY added_ts DESC").fetchall()]


def add(row: dict) -> None:
    with _LOCK, _conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO messages
               (chat_id,message_id,ts,user_id,user_name,chat_type,chat_name,
                thread_id,text,reply_to_message_id,reply_to_author)
               VALUES(:chat_id,:message_id,:ts,:user_id,:user_name,:chat_type,
                :chat_name,:thread_id,:text,:reply_to_message_id,:reply_to_author)""",
            row,
        )


def latest_chat() -> str | None:
    with _conn() as c:
        r = c.execute("SELECT chat_id FROM messages ORDER BY ts DESC LIMIT 1").fetchone()
        return r["chat_id"] if r else None


def recent(chat_id: str, limit: int, since: float | None = None) -> list[dict]:
    q, args = "SELECT * FROM messages WHERE chat_id=?", [chat_id]
    if since:
        q += " AND ts>=?"
        args.append(since)
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()][::-1]


def search(query: str, chat_id: str | None, limit: int) -> list[dict]:
    with _conn() as c:
        if chat_id:
            rows = c.execute(
                "SELECT * FROM messages WHERE chat_id=? AND text LIKE ? ORDER BY ts DESC LIMIT ?",
                (chat_id, f"%{query}%", limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM messages WHERE text LIKE ? ORDER BY ts DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
        return [dict(r) for r in rows]


def thread(chat_id: str, message_id: str) -> list[dict]:
    """Reconstruct a thread: ancestors (up the reply chain) + descendants (replies)."""
    with _conn() as c:
        rows = {r["message_id"]: dict(r)
                for r in c.execute("SELECT * FROM messages WHERE chat_id=?", (chat_id,)).fetchall()}
    if message_id not in rows:
        return []
    keep: dict[str, dict] = {}
    # up: follow reply_to to the root
    cur = message_id
    seen = set()
    while cur and cur in rows and cur not in seen:
        seen.add(cur)
        keep[cur] = rows[cur]
        cur = rows[cur].get("reply_to_message_id")
    # down: BFS over messages replying to anything already kept
    changed = True
    while changed:
        changed = False
        for mid, r in rows.items():
            if mid not in keep and r.get("reply_to_message_id") in keep:
                keep[mid] = r
                changed = True
    return sorted(keep.values(), key=lambda r: (r.get("ts") or 0))
