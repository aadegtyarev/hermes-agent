"""Google Docs plugin — READ-ONLY.

Registers into the ``google_docs`` toolset:
  * ``gdoc_read``     — read a Google Doc's text by URL/id (documents.readonly).
    ``include_suggestions=true`` marks pending Suggesting-mode edits inline
    instead of reading them as already-accepted text.
  * ``gdoc_comments`` — sidebar comment threads (Drive Comments API,
    drive.readonly) — a separate mechanism from Suggesting-mode edits above,
    not visible to gdoc_read at all.
  * ``gdrive_search`` — full-text Drive search by content/name, TTL-cached
    (drive.readonly).

Credentials are resolved parent-side from a mounted read-only token /
service-account file (see _gauth). No write tools exist.
"""
from __future__ import annotations

from .tools import (
    GDOC_COMMENTS_SCHEMA,
    GDOC_READ_SCHEMA,
    GDRIVE_SEARCH_SCHEMA,
    check_available,
    handle_gdoc_comments,
    handle_gdoc_read,
    handle_gdrive_search,
)

_TOOLS = (
    ("gdoc_read", GDOC_READ_SCHEMA, handle_gdoc_read, "📄"),
    ("gdoc_comments", GDOC_COMMENTS_SCHEMA, handle_gdoc_comments, "💬"),
    ("gdrive_search", GDRIVE_SEARCH_SCHEMA, handle_gdrive_search, "🔎"),
)


def register(ctx) -> None:
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="google_docs",
            schema=schema,
            handler=handler,
            check_fn=check_available,
            emoji=emoji,
        )
