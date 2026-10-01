"""Per-agent SQLite store of incoming Telegram messages ($HERMES_HOME/telegram.db).

The Bot API can't fetch history, so we persist messages as they arrive (via the
ingest hook) and reconstruct threads / recent / search from here. Isolated per
agent (its own data volume).

``recent()``/``search()`` double as a topic-report/digest source: pass
``since``/``until`` + ``after_id`` (cursor, 0 = start of the window) to walk an
entire time window page by page (SQLite ``rowid`` order — monotonic insertion
order) instead of the plain "last N" behavior. ``count()`` is the cheap
upfront check for how much there is before paging a large window.
"""
from __future__ import annotations

import contextlib
import re
import sqlite3
import threading
import time

from hermes_constants import get_hermes_home

_LOCK = threading.Lock()


def _db_path() -> str:
    return str(get_hermes_home() / "telegram.db")


@contextlib.contextmanager
def _conn():
    """A ``with _conn() as c:`` connection that actually closes on exit.

    ``sqlite3.Connection`` is its own context manager, but its ``__exit__``
    only commits/rolls back the transaction — it does NOT close the
    connection (verified; this is documented Python behavior, easy to miss).
    Every ``with _conn() as c:`` call site in this file (there are ~15 —
    every public function below) was therefore leaking one file descriptor
    per call, completely independent of the client-chat feature. In a
    long-lived gateway process ingesting messages continuously, that adds up
    to real fd exhaustion over days/weeks — and the isolation guards in
    __init__.py fail OPEN when a store call raises, so hitting the fd limit
    doesn't just break ingestion, it silently disables every isolation check
    too. This wrapper preserves the exact ``with _conn() as c:`` call-site
    syntax everywhere else in this file (zero other changes needed) while
    actually closing the connection, success or failure.
    """
    c = sqlite3.connect(_db_path(), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def _add_column_if_missing(c: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Idempotent ``ALTER TABLE ADD COLUMN`` for a table that predates the column."""
    existing = {row[1] for row in c.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


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

        # Full-text index over messages.text (FTS5, unicode61 tokenizer — folds
        # case correctly for Cyrillic, unlike plain SQL LIKE, and tokenizes
        # instead of doing a raw substring scan). External-content table keyed
        # by the base table's own (implicit) rowid, kept in sync by triggers so
        # every write path (just `add()` today) stays a single INSERT OR
        # REPLACE with no FTS-specific bookkeeping at the call site.
        c.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5("
            "text, content='messages', content_rowid='rowid')"
        )
        c.execute(
            "CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN "
            "INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text); END"
        )
        c.execute(
            "CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN "
            "INSERT INTO messages_fts(messages_fts, rowid, text) VALUES('delete', old.rowid, old.text); END"
        )
        c.execute(
            "CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN "
            "INSERT INTO messages_fts(messages_fts, rowid, text) VALUES('delete', old.rowid, old.text); "
            "INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text); END"
        )
        # One-time backfill for rows ingested before this table existed — the
        # triggers above only cover rows written from here on.
        #
        # NOTE on a real bug this replaced: for an external-content FTS5 table
        # (content='messages'), a plain `SELECT ... FROM messages_fts` — even
        # `COUNT(*)`, even with no WHERE at all — passes straight through to
        # the content table regardless of whether the actual search index
        # has anything in it. A prior version of this backfill compared
        # `COUNT(*) FROM messages_fts` against `COUNT(*) FROM messages` (and,
        # separately, a LEFT JOIN against messages_fts) to decide whether to
        # backfill — both always report "already populated" even on a
        # freshly created, completely empty index, because the row identity
        # pass-through has nothing to do with whether MATCH can find
        # anything. The result: on the very first deploy of this table
        # against an already-populated telegram.db, every row read back as
        # "already indexed" and the backfill silently never ran — MATCH
        # found nothing for ANY query, for ANY word, including exact
        # substrings verified present in the raw text. Only newly-added rows
        # (via the triggers) were ever searchable.
        #
        # Fix: track completion explicitly in a real (non-virtual) table,
        # and use FTS5's own `INSERT INTO messages_fts(messages_fts) VALUES
        # ('rebuild')` command — the documented, correct way to (re)populate
        # an external-content index from scratch — instead of hand-rolling
        # a row-by-row copy whose "already done" detection doesn't work for
        # this table type.
        c.execute(
            "CREATE TABLE IF NOT EXISTS fts_migration_state(key TEXT PRIMARY KEY, done_at REAL)"
        )
        already_rebuilt = c.execute(
            "SELECT 1 FROM fts_migration_state WHERE key='messages_fts_rebuilt'"
        ).fetchone()
        if not already_rebuilt:
            c.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
            c.execute(
                "INSERT INTO fts_migration_state(key, done_at) VALUES('messages_fts_rebuilt', ?)",
                (time.time(),),
            )
        # Auto-collected DM allowlist: users seen in / confirmed members of work chats.
        c.execute(
            """CREATE TABLE IF NOT EXISTS dm_allowed(
                user_id TEXT PRIMARY KEY, user_name TEXT, source_chat TEXT, added_ts REAL)"""
        )
        # Runtime chat allowlist: chats enrolled via admin commands (no file edits).
        # mode ∈ {work, readonly, client}; work/readonly unioned with the
        # TELEGRAM_*_CHATS env at gate time. "client" chats (isolated
        # per-partner/per-customer chats — see partner_programs below) carry a
        # ``program`` tag; NULL for work/readonly.
        c.execute(
            """CREATE TABLE IF NOT EXISTS chats_allowed(
                chat_id TEXT PRIMARY KEY, mode TEXT, title TEXT, added_by TEXT, added_ts REAL,
                program TEXT)"""
        )
        _add_column_if_missing(c, "chats_allowed", "program", "TEXT")

        # A "program" is a team's own chat (journalists, support engineers, ...)
        # plus every "client" chat registered under it. Membership in the
        # program's own chat_id IS the trust/operator signal — checked live via
        # getChatMember, not a separately-maintained admin list (see
        # _user_is_member_of_chat in __init__.py). One chat_id can be a
        # program's team chat XOR a client chat, never both — enforced at the
        # call sites in __init__.py, not here.
        c.execute(
            """CREATE TABLE IF NOT EXISTS partner_programs(
                program TEXT PRIMARY KEY, chat_id TEXT, created_by TEXT, created_ts REAL)"""
        )

        # Google Docs/Sheets links observed in a client chat's own messages —
        # the ONLY documents that chat's session may ever open (see the
        # gdoc pre_tool_call guard). Populated incrementally at ingest time,
        # never pre-registered — a client chat may share several documents
        # over its lifetime, and which ones matter isn't known at registration.
        c.execute(
            """CREATE TABLE IF NOT EXISTS chat_doc_links(
                chat_id TEXT, doc_id TEXT, first_seen_ts REAL, PRIMARY KEY(chat_id, doc_id))"""
        )

        # Per-chat escalation cooldown (escalate_to_team / the batch reviewer's
        # own flag path share this — one clock per chat, not per caller).
        c.execute(
            """CREATE TABLE IF NOT EXISTS chat_escalations(
                chat_id TEXT PRIMARY KEY, last_escalated_ts REAL)"""
        )

        # Batch reviewer's per-chat "last checked" clock (client_chat_batch_
        # review.py, a standalone cron script, also creates this defensively
        # since it runs in its own subprocess — kept here too so `hermes
        # sessions digest`-style introspection and tests see the full schema
        # from a single store.init() call).
        c.execute(
            """CREATE TABLE IF NOT EXISTS chat_review_state(
                chat_id TEXT PRIMARY KEY, last_reviewed_ts REAL)"""
        )


def set_chat(chat_id: str, mode: str, title: str = "", added_by: str = "", program: str | None = None) -> None:
    import time
    if not chat_id or mode not in ("work", "readonly", "client"):
        return
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO chats_allowed(chat_id,mode,title,added_by,added_ts,program) "
            "VALUES(?,?,?,?,?,?)",
            (str(chat_id), mode, title, str(added_by), time.time(), program),
        )


def remove_chat(chat_id: str) -> bool:
    if not chat_id:
        return False
    with _LOCK, _conn() as c:
        return c.execute("DELETE FROM chats_allowed WHERE chat_id=?", (str(chat_id),)).rowcount > 0


def forget_chat_state(chat_id: str) -> None:
    """Drop every per-chat table this plugin accumulates for ``chat_id`` —
    doc-link allowlist, escalation cooldown, batch-review clock. Without
    this, /hermes_forget only removed the chats_allowed row; re-registering
    the SAME chat_id later (a new client chat, possibly under a different
    program) would inherit the PREVIOUS occupant's gdoc allowlist and
    escalation/review timestamps — a stale doc-id allowlist is the concerning
    one (a new partner's chat trusting documents an unrelated prior chat
    once linked)."""
    if not chat_id:
        return
    with _LOCK, _conn() as c:
        c.execute("DELETE FROM chat_doc_links WHERE chat_id=?", (str(chat_id),))
        c.execute("DELETE FROM chat_escalations WHERE chat_id=?", (str(chat_id),))
        c.execute("DELETE FROM chat_review_state WHERE chat_id=?", (str(chat_id),))


def chats_by_mode(mode: str) -> set[str]:
    with _conn() as c:
        return {r["chat_id"] for r in
                c.execute("SELECT chat_id FROM chats_allowed WHERE mode=?", (mode,)).fetchall()}


def chat_mode(chat_id: str) -> str | None:
    """The registered mode (work/readonly/client) of chat_id, or None if unregistered."""
    if not chat_id:
        return None
    with _conn() as c:
        r = c.execute("SELECT mode FROM chats_allowed WHERE chat_id=?", (str(chat_id),)).fetchone()
        return r["mode"] if r else None


def chat_program(chat_id: str) -> str | None:
    """The program a client chat is registered under, or None."""
    if not chat_id:
        return None
    with _conn() as c:
        r = c.execute(
            "SELECT program FROM chats_allowed WHERE chat_id=? AND mode='client'", (str(chat_id),)
        ).fetchone()
        return r["program"] if r else None


def list_chats() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT chat_id,mode,title,added_by,program FROM chats_allowed ORDER BY added_ts DESC").fetchall()]


# ── Programs: a team's own chat + every client chat registered under it ────


_PROGRAM_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def create_program(program: str, chat_id: str, created_by: str) -> bool:
    """Bind ``program`` to ``chat_id`` as that program's team chat.

    Returns False (no-op) if ``program`` isn't lowercase-alnum/-/_ (matches
    the style already used for every program name in this feature —
    "journalist-partners", "support-clients" — and avoids a name containing
    spaces/punctuation silently becoming part of other tables' primary keys
    unchecked), if the program name is already taken, or if ``chat_id`` is
    already registered as something else (a client chat, or another
    program's team chat) — a chat is exactly one thing. Callers should
    normalize case before calling (see __init__.py's _handle_hermes_program)
    since this is case-SENSITIVE storage — normalizing here too would let a
    caller's own "does it already exist" lookup (done before calling this)
    disagree with what actually gets stored.
    """
    import time
    if not program or not chat_id or not _PROGRAM_NAME_RE.match(program):
        return False
    with _LOCK, _conn() as c:
        if c.execute("SELECT 1 FROM partner_programs WHERE program=?", (program,)).fetchone():
            return False
        if c.execute("SELECT 1 FROM partner_programs WHERE chat_id=?", (str(chat_id),)).fetchone():
            return False
        if c.execute("SELECT 1 FROM chats_allowed WHERE chat_id=?", (str(chat_id),)).fetchone():
            return False
        c.execute(
            "INSERT INTO partner_programs(program,chat_id,created_by,created_ts) VALUES(?,?,?,?)",
            (program, str(chat_id), str(created_by), time.time()),
        )
        return True


def programs() -> dict[str, str]:
    """{program_name: team_chat_id} for every registered program."""
    with _conn() as c:
        return {r["program"]: r["chat_id"] for r in c.execute(
            "SELECT program, chat_id FROM partner_programs").fetchall()}


def program_chat_id(program: str) -> str | None:
    with _conn() as c:
        r = c.execute("SELECT chat_id FROM partner_programs WHERE program=?", (program,)).fetchone()
        return r["chat_id"] if r else None


def is_program_team_chat(chat_id: str) -> bool:
    if not chat_id:
        return False
    with _conn() as c:
        return c.execute(
            "SELECT 1 FROM partner_programs WHERE chat_id=?", (str(chat_id),)
        ).fetchone() is not None


def program_by_team_chat(chat_id: str) -> str | None:
    """Reverse of program_chat_id: the program whose team chat is ``chat_id``."""
    if not chat_id:
        return None
    with _conn() as c:
        r = c.execute(
            "SELECT program FROM partner_programs WHERE chat_id=?", (str(chat_id),)
        ).fetchone()
        return r["program"] if r else None


def program_client_chats(program: str) -> list[dict]:
    """Every client chat under ``program``, newest-activity first, each with
    its title and activity/review metadata — the enumeration a program's team
    chat needs to sweep or digest its clients top-down.
    """
    if not program:
        return []
    with _conn() as c:
        rows = c.execute(
            """SELECT ca.chat_id AS chat_id, ca.title AS title,
                      MAX(m.ts) AS last_ts, COUNT(m.rowid) AS msg_count,
                      crs.last_reviewed_ts AS last_reviewed_ts
                 FROM chats_allowed ca
                 LEFT JOIN messages m ON m.chat_id = ca.chat_id
                 LEFT JOIN chat_review_state crs ON crs.chat_id = ca.chat_id
                WHERE ca.mode='client' AND ca.program=?
                GROUP BY ca.chat_id
                ORDER BY last_ts DESC NULLS LAST""",
            (program,),
        ).fetchall()
        return [dict(r) for r in rows]


def search_in_chats(query: str, chat_ids: list[str], limit: int,
                    since: float | None = None) -> list[dict]:
    """FTS search over messages, restricted to a set of chat_ids (one query,
    not one per chat). Returns matching messages newest-first."""
    fts_query = _fts5_prefix_query(query)
    if fts_query is None or not chat_ids:
        return []
    placeholders = ",".join("?" for _ in chat_ids)
    where = f"messages_fts MATCH ? AND m.chat_id IN ({placeholders})"
    args: list = [fts_query, *[str(c) for c in chat_ids]]
    if since is not None:
        where += " AND m.ts>=?"
        args.append(since)
    fetch_limit = max(1, min(int(limit), 500))
    with _conn() as c:
        rows = c.execute(
            f"SELECT m.rowid, m.* FROM messages_fts "
            f"JOIN messages m ON m.rowid = messages_fts.rowid "
            f"WHERE {where} ORDER BY m.rowid DESC LIMIT ?",
            [*args, fetch_limit],
        ).fetchall()
        return [dict(r) for r in rows]


def remove_program_by_chat(chat_id: str) -> str | None:
    """Drop the program whose team chat is ``chat_id``. Returns its name, or None."""
    if not chat_id:
        return None
    with _LOCK, _conn() as c:
        r = c.execute("SELECT program FROM partner_programs WHERE chat_id=?", (str(chat_id),)).fetchone()
        if not r:
            return None
        c.execute("DELETE FROM partner_programs WHERE chat_id=?", (str(chat_id),))
        return r["program"]


# ── Google Docs/Sheets links observed in a client chat ──────────────────────


def link_chat_doc(chat_id: str, doc_id: str) -> None:
    import time
    if not chat_id or not doc_id:
        return
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO chat_doc_links(chat_id,doc_id,first_seen_ts) VALUES(?,?,?)",
            (str(chat_id), doc_id, time.time()),
        )


def chat_doc_ids(chat_id: str) -> set[str]:
    with _conn() as c:
        return {r["doc_id"] for r in c.execute(
            "SELECT doc_id FROM chat_doc_links WHERE chat_id=?", (str(chat_id),)).fetchall()}


# ── Resolving which chat a tool call's session belongs to ───────────────────


def origin_chat_id(session_id: str, _depth: int = 0) -> str | None:
    """Resolve the Telegram chat_id the CURRENT tool call's session belongs to.

    ``pre_tool_call`` hooks and tool handlers alike get ``session_id`` (the
    real, already-persisted core session id — ``agent.session_id``, set on
    every gateway turn), not chat_id directly (neither
    ``model_tools.handle_function_call``'s dispatch signature nor the tool
    registry's own handler kwargs carry a chat_id param). hermes-agent's own
    SessionDB already records chat_id per session (gateway/session.py's
    build_session_key scopes a group session on chat_id), so a cheap
    read-only lookup gets us from one to the other without any new core
    plumbing. Returns None for a CLI/non-gateway session, an unresolvable id,
    or on any lookup error — fail-open to "can't tell"; callers only restrict
    when this resolves AND the resolved chat is registered as ``client`` mode.

    Follows ``parent_session_id`` when the row's own ``chat_id`` is empty —
    mid-turn context compression (agent/conversation_compression.py) rotates
    ``agent.session_id`` to a freshly-created row that records only
    ``parent_session_id``; the gateway backfills that row's own ``chat_id``
    only AFTER the turn returns (gateway/run.py). Without following the
    chain, every tool call for the rest of a compressed turn would resolve to
    "unknown chat" and silently lose the client-chat isolation guard for
    exactly the long-running conversations this feature targets. Depth-capped
    against a corrupted/cyclic parent chain.

    Also checks the resolved row's own ``source`` is ``"telegram"`` — a
    chat_id collision with a differently-sourced session (unlikely, but
    cheap to rule out) must not be treated as a Telegram chat.

    Lives here (not in ``__init__.py``, where it originated) so both the
    ``_pre_tool_call`` isolation guard AND ``tools.py``'s escalation handler
    can use it without a circular import between the two.
    """
    if not session_id or _depth > 5:
        return None
    try:
        from hermes_state import SessionDB
        db = SessionDB(read_only=True)
        try:
            row = db.get_session(session_id)
        finally:
            db.close()
        if not row:
            return None
        chat_id = str(row.get("chat_id") or "").strip()
        if chat_id:
            if str(row.get("source") or "").strip().lower() != "telegram":
                return None
            return chat_id
        parent_id = str(row.get("parent_session_id") or "").strip()
        if parent_id and parent_id != session_id:
            return origin_chat_id(parent_id, _depth + 1)
        return None
    except Exception:
        return None


# ── Escalation cooldown + chat metadata for the escalation tool ─────────────


def chat_title(chat_id: str) -> str | None:
    if not chat_id:
        return None
    with _conn() as c:
        r = c.execute("SELECT title FROM chats_allowed WHERE chat_id=?", (str(chat_id),)).fetchone()
        return (r["title"] or None) if r else None


def last_escalation_ts(chat_id: str) -> float | None:
    if not chat_id:
        return None
    with _conn() as c:
        r = c.execute(
            "SELECT last_escalated_ts FROM chat_escalations WHERE chat_id=?", (str(chat_id),)
        ).fetchone()
        return r["last_escalated_ts"] if r else None


def record_escalation(chat_id: str, ts: float | None = None) -> None:
    if not chat_id:
        return
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO chat_escalations(chat_id,last_escalated_ts) VALUES(?,?)",
            (str(chat_id), ts if ts is not None else time.time()),
        )


def mark_reviewed(chat_id: str, ts: float | None = None) -> None:
    """Record that ``chat_id`` was just included in a batch-review digest.

    Normally written by client_chat_batch_review.py's own raw-sqlite insert
    (it's a standalone subprocess, doesn't import this module) — exposed
    here too so tests (and any future in-process caller) don't need to
    reach into telegram.db's schema directly to simulate "this chat was
    reviewed this tick", which handle_partner_flag_chats' recency check
    depends on.
    """
    if not chat_id:
        return
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO chat_review_state(chat_id, last_reviewed_ts) VALUES(?,?)",
            (str(chat_id), ts if ts is not None else time.time()),
        )


def last_reviewed_ts(chat_id: str) -> float | None:
    """When client_chat_batch_review.py last included ``chat_id`` in a
    digest — used by ``handle_partner_flag_chats`` to confirm a flagged
    chat_id was ACTUALLY part of the current/most recent review batch, not
    an arbitrary registered client chat the model (or injected partner text
    it was shown) picked. See the ``_RECENTLY_REVIEWED_WINDOW`` check at
    that call site."""
    if not chat_id:
        return None
    with _conn() as c:
        r = c.execute(
            "SELECT last_reviewed_ts FROM chat_review_state WHERE chat_id=?", (str(chat_id),)
        ).fetchone()
        return r["last_reviewed_ts"] if r else None


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


def recent(chat_id: str, limit: int, since: float | None = None, until: float | None = None,
           after_id: int | None = None) -> dict:
    """Messages for a chat.

    ``after_id=None`` (default): the most recent ``limit`` messages in the
    window, oldest->newest — today's "what just happened" behavior, unchanged.

    ``after_id`` given (0 = from the start of the window): forward,
    chronological pagination — the next ``limit`` messages strictly after
    that cursor (SQLite ``rowid``, monotonic insertion order). Keep paging
    with the returned ``next_cursor`` while ``has_more`` is true to walk an
    entire time window exhaustively (a digest/report) without missing or
    duplicating rows — the thing plain LIMIT-capped recent/search can't do
    for a window bigger than the cap.
    """
    where, args = "chat_id=?", [chat_id]
    if since is not None:
        where += " AND ts>=?"
        args.append(since)
    if until is not None:
        where += " AND ts<=?"
        args.append(until)

    paginating = after_id is not None
    if paginating:
        where += " AND rowid>?"
        args.append(after_id)
        order = "ORDER BY rowid ASC"
    else:
        order = "ORDER BY rowid DESC"

    fetch_limit = max(1, min(int(limit), 500))
    with _conn() as c:
        rows = [dict(r) for r in c.execute(
            f"SELECT rowid, * FROM messages WHERE {where} {order} LIMIT ?",
            [*args, fetch_limit + 1],
        ).fetchall()]

    has_more = len(rows) > fetch_limit
    rows = rows[:fetch_limit]
    if not paginating:
        rows = rows[::-1]  # DESC fetch -> chronological order for display

    next_cursor = rows[-1]["rowid"] if (has_more and paginating) else None
    return {"messages": rows, "has_more": has_more, "next_cursor": next_cursor}


def count(chat_id: str | None, since: float | None = None, until: float | None = None,
          query: str | None = None) -> dict:
    """Total matching messages / time span / approx size for a chat (or, with
    chat_id=None, every chat) + window — a cheap upfront check before paging
    through a large window. Pass ``query`` to count search matches (same FTS5
    match as :func:`search`) instead of every message in the window."""
    where, args = "1=1", []
    if chat_id:
        where += " AND m.chat_id=?"
        args.append(chat_id)
    if since is not None:
        where += " AND m.ts>=?"
        args.append(since)
    if until is not None:
        where += " AND m.ts<=?"
        args.append(until)

    fts_query = _fts5_prefix_query(query) if query else None
    if fts_query:
        where = "messages_fts MATCH ? AND " + where
        args = [fts_query, *args]
        from_clause = "messages_fts JOIN messages m ON m.rowid = messages_fts.rowid"
    elif query:
        # A query was given but tokenized to nothing (punctuation-only) —
        # no message could ever match; skip straight to a zero result.
        return {"total_count": 0, "oldest_ts": None, "newest_ts": None, "approx_chars": 0}
    else:
        from_clause = "messages m"

    with _conn() as c:
        r = c.execute(
            f"SELECT COUNT(*) AS n, MIN(m.ts) AS oldest, MAX(m.ts) AS newest, "
            f"SUM(LENGTH(m.text)) AS chars FROM {from_clause} WHERE {where}",
            args,
        ).fetchone()
    return {
        "total_count": r["n"] or 0,
        "oldest_ts": r["oldest"],
        "newest_ts": r["newest"],
        "approx_chars": r["chars"] or 0,
    }


_FTS5_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _fts5_prefix_query(text: str) -> str | None:
    """Build a safe FTS5 query: every word in ``text`` becomes a prefix term,
    ANDed together (FTS5's default for space-separated bare terms).

    Two reasons for prefix-per-word rather than an exact or phrase match:

    - Russian is heavily inflected — "прошивка"/"прошивку"/"прошивки" differ
      only in their ending, and FTS5's unicode61 tokenizer has no stemmer.
      Searching "прошив*" matches all of them; an exact-word match would
      require the searcher to guess the exact grammatical form used in the
      message. Case-folding (unlike plain SQL LIKE) works correctly either
      way — this is on top of that fix, not instead of it.
    - Tokens are extracted with a plain word-character regex, never fed to
      FTS5 raw, so arbitrary punctuation in the input (quotes, colons,
      parens, a stray ``*``) can never produce invalid FTS5 syntax.
    - Each token is quoted (``"word"*``, not bare ``word*``) so a token that
      happens to BE an FTS5 keyword ("or", "and", "not" — case-insensitively;
      a query like "a or b" is entirely plausible chat text) is forced to
      parse as a literal search term instead of a boolean operator, which a
      bare ``OR*``/``AND*``/``NOT*`` raises a syntax error on (verified: the
      quoted form still supports the prefix ``*``, unlike quoting the whole
      multi-word phrase, which does not).

    Returns ``None`` for a punctuation-only/empty query (nothing to search).
    """
    tokens = _FTS5_TOKEN_RE.findall(text)
    if not tokens:
        return None
    return " ".join(f'"{t}"*' for t in tokens)


def search(query: str, chat_id: str | None, limit: int, since: float | None = None,
           until: float | None = None, after_id: int | None = None) -> dict:
    """Full-text search across stored messages (SQLite FTS5 — tokenized,
    case-folds correctly for Cyrillic, unlike a plain LIKE substring scan).

    Same ``after_id``/pagination contract as :func:`recent` — omit it for a
    plain "most recent N matches" search, pass it (0 to start) to walk every
    match in a time window exhaustively.
    """
    fts_query = _fts5_prefix_query(query)
    if fts_query is None:
        return {"messages": [], "has_more": False, "next_cursor": None}
    where, args = "messages_fts MATCH ?", [fts_query]
    if chat_id:
        where += " AND m.chat_id=?"
        args.append(chat_id)
    if since is not None:
        where += " AND m.ts>=?"
        args.append(since)
    if until is not None:
        where += " AND m.ts<=?"
        args.append(until)

    paginating = after_id is not None
    if paginating:
        where += " AND m.rowid>?"
        args.append(after_id)
        order = "ORDER BY m.rowid ASC"
    else:
        order = "ORDER BY m.rowid DESC"

    fetch_limit = max(1, min(int(limit), 500))
    with _conn() as c:
        rows = [dict(r) for r in c.execute(
            f"SELECT m.rowid, m.* FROM messages_fts "
            f"JOIN messages m ON m.rowid = messages_fts.rowid "
            f"WHERE {where} {order} LIMIT ?",
            [*args, fetch_limit + 1],
        ).fetchall()]

    has_more = len(rows) > fetch_limit
    rows = rows[:fetch_limit]
    if not paginating:
        rows = rows[::-1]

    next_cursor = rows[-1]["rowid"] if (has_more and paginating) else None
    return {"messages": rows, "has_more": has_more, "next_cursor": next_cursor}


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
