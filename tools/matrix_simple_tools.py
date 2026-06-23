"""
matrix_simple_tools.py — Matrix (simple) platform tools.

Provides agent-callable functions for the matrix-simple Hermes platform:
  - ms_list_rooms       : list joined rooms with names and last messages
  - ms_read_history     : read message history from a room
  - ms_search           : search messages across rooms
  - ms_find_room        : find room by name and optionally send a message

Uses env vars MATRIX_HOMESERVER / MATRIX_USERNAME / MATRIX_PASSWORD
to authenticate. Token is obtained once per process and reused.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_CACHED_TOKEN: str | None = None
_CACHED_HOMESERVER: str = ""


def _get_homeserver() -> str:
    return os.getenv("MATRIX_HOMESERVER", "").strip()


def _get_token() -> str:
    """Get or refresh Matrix access token."""
    global _CACHED_TOKEN, _CACHED_HOMESERVER
    hs = _get_homeserver()
    if _CACHED_TOKEN and _CACHED_HOMESERVER == hs:
        return _CACHED_TOKEN

    user = os.getenv("MATRIX_USERNAME", "").strip()
    pw = os.getenv("MATRIX_PASSWORD", "").strip()
    if not (hs and user and pw):
        raise RuntimeError("Matrix env vars not set")

    login_url = f"{hs}/_matrix/client/v3/login"
    login_data = json.dumps({
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": user},
        "password": pw,
    }).encode()
    req = urllib.request.Request(login_url, data=login_data,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read().decode())
    _CACHED_TOKEN = resp["access_token"]
    _CACHED_HOMESERVER = hs
    return _CACHED_TOKEN


def _api_get(path: str) -> dict:
    hs = _get_homeserver()
    token = _get_token()
    url = f"{hs}/_matrix/client/v3/{path}"
    req = urllib.request.Request(url,
        headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _api_post(path: str, body: dict) -> dict:
    hs = _get_homeserver()
    token = _get_token()
    url = f"{hs}/_matrix/client/v3/{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

def _list_rooms() -> dict:
    """List joined rooms with names and last messages."""
    try:
        data = _api_get("sync?timeout=0")
        join = data.get("rooms", {}).get("join", {})
        rooms = []
        for rid, rdata in join.items():
            name = rid
            for ev in rdata.get("state", {}).get("events", []):
                if ev.get("type") == "m.room.name":
                    name = ev["content"].get("name", rid)
                    break
                elif ev.get("type") == "m.room.canonical_alias":
                    name = ev["content"].get("alias", rid)
            last = None
            timeline = rdata.get("timeline", {}).get("events", [])
            if timeline:
                msg_events = [e for e in timeline if e.get("type") == "m.room.message"]
                if msg_events:
                    last_ev = msg_events[-1]
                    sender = last_ev.get("sender", "?").split(":")[0].lstrip("@")
                    body = last_ev.get("content", {}).get("body", "")[:80]
                    ts = last_ev.get("origin_server_ts", 0)
                    last = {"sender": sender, "body": body, "time": _fmt_ts(ts)}
            rooms.append({
                "name": name,
                "room_id": rid,
                "last_message": last,
            })
        return {"success": True, "rooms": rooms}
    except Exception as e:
        logger.exception("ms_list_rooms failed")
        return {"success": False, "error": str(e)}


def _read_history(room_id: str, limit: int = 50) -> dict:
    """Read message history from a room."""
    try:
        data = _api_get(f"rooms/{room_id}/messages?dir=b&limit={limit}")
        chunk = data.get("chunk", [])
        messages = []
        for ev in reversed(chunk):
            if ev.get("type") != "m.room.message":
                continue
            sender = ev["sender"].split(":")[0].lstrip("@")
            body = ev["content"].get("body", "")
            ts = ev.get("origin_server_ts", 0)
            messages.append({
                "sender": sender,
                "body": body,
                "time": _fmt_ts(ts),
                "event_id": ev.get("event_id", ""),
            })
        return {
            "success": True,
            "room_id": room_id,
            "messages": messages,
            "end_token": data.get("end", ""),
        }
    except Exception as e:
        logger.exception("ms_read_history failed")
        return {"success": False, "error": str(e)}


def _search(query: str) -> dict:
    """Search messages across all joined rooms."""
    try:
        body = {
            "search_categories": {
                "room_events": {
                    "search_term": query,
                    "order_by": "recent",
                    "event_context": {
                        "before_limit": 1,
                        "after_limit": 1,
                        "include_profile": True,
                    },
                },
            },
        }
        data = _api_post("search", body)
        results = data.get("search_categories", {}).get("room_events", {}).get("results", [])
        hits = []
        for r in results:
            ev = r.get("result", {})
            hits.append({
                "sender": ev.get("sender", "?").split(":")[0].lstrip("@"),
                "body": ev.get("content", {}).get("body", "")[:200],
                "room_id": ev.get("room_id", ""),
                "time": _fmt_ts(ev.get("origin_server_ts", 0)),
            })
        return {"success": True, "query": query, "results": hits}
    except Exception as e:
        logger.exception("ms_search failed")
        return {"success": False, "error": str(e)}


def _find_room(name: str, message: str = "") -> dict:
    """Find a room by name (substring match) and optionally send a message."""
    try:
        data = _api_get("sync?timeout=0")
        join = data.get("rooms", {}).get("join", {})
        target = name.lower()
        found = None
        for rid, rdata in join.items():
            for ev in rdata.get("state", {}).get("events", []):
                if ev.get("type") == "m.room.name":
                    if target in ev["content"].get("name", "").lower():
                        found = rid
                elif ev.get("type") == "m.room.canonical_alias":
                    if target in ev["content"].get("alias", "").lower():
                        found = rid
            if found:
                break

        if not found:
            return {"success": False, "error": f"Room matching '{name}' not found"}

        result = {"success": True, "room_id": found}

        if message:
            import uuid
            txn = str(uuid.uuid4())
            hs = _get_homeserver()
            token = _get_token()
            send_url = f"{hs}/_matrix/client/v3/rooms/{found}/send/m.room.message/{txn}"
            send_data = json.dumps({
                "msgtype": "m.text",
                "body": message,
            }).encode()
            req = urllib.request.Request(send_url, data=send_data, method="PUT",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                })
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read().decode())
            result["event_id"] = resp.get("event_id", "")

        return result
    except Exception as e:
        logger.exception("ms_find_room failed")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _handle_ms_list_rooms(args, **kw):
    return _list_rooms()


def _handle_ms_read_history(args, **kw):
    return _read_history(
        room_id=args.get("room_id", ""),
        limit=args.get("limit", 50),
    )


def _handle_ms_search(args, **kw):
    return _search(query=args.get("query", ""))


def _handle_ms_find_room(args, **kw):
    return _find_room(
        name=args.get("name", ""),
        message=args.get("message", ""),
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

from tools.registry import registry, tool_result  # noqa: E402


def _check_matrix_simple():
    """Toolset availability — True when Matrix env vars are set."""
    hs = os.getenv("MATRIX_HOMESERVER", "").strip()
    user = os.getenv("MATRIX_USERNAME", "").strip()
    pw = os.getenv("MATRIX_PASSWORD", "").strip()
    return bool(hs and user and pw)


_TOOLSET = "matrix-simple"

registry.register(
    name="ms_list_rooms",
    toolset=_TOOLSET,
    schema={
        "name": "ms_list_rooms",
        "description": (
            "List all Matrix rooms you are joined to, with room names, "
            "IDs, and the most recent message in each room."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    handler=_handle_ms_list_rooms,
    check_fn=_check_matrix_simple,
    is_async=False,
    emoji="📋",
)

registry.register(
    name="ms_read_history",
    toolset=_TOOLSET,
    schema={
        "name": "ms_read_history",
        "description": (
            "Read the most recent messages from a Matrix room. "
            "Use this to catch up on conversations or check what "
            "was discussed. Returns messages in chronological order."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "room_id": {
                    "type": "string",
                    "description": "The Matrix room ID (e.g. !abc123:matrix.adsrv.ru).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of messages to return (default 50, max 200).",
                },
            },
            "required": ["room_id"],
        },
    },
    handler=_handle_ms_read_history,
    check_fn=_check_matrix_simple,
    is_async=False,
    emoji="📜",
)

registry.register(
    name="ms_search",
    toolset=_TOOLSET,
    schema={
        "name": "ms_search",
        "description": (
            "Search across all joined Matrix rooms for messages "
            "containing a keyword or phrase. Useful for finding past "
            "discussions, references, or specific information."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search term or phrase to look for.",
                },
            },
            "required": ["query"],
        },
    },
    handler=_handle_ms_search,
    check_fn=_check_matrix_simple,
    is_async=False,
    emoji="🔍",
)

registry.register(
    name="ms_find_room",
    toolset=_TOOLSET,
    schema={
        "name": "ms_find_room",
        "description": (
            "Find a Matrix room by name (substring match) and optionally "
            "send a message to it. Use this when you need to notify a "
            "specific room or find a room's ID for other operations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Room name to search for (substring, case-insensitive).",
                },
                "message": {
                    "type": "string",
                    "description": "Optional message to send to the found room.",
                },
            },
            "required": ["name"],
        },
    },
    handler=_handle_ms_find_room,
    check_fn=_check_matrix_simple,
    is_async=False,
    emoji="🔎",
)
