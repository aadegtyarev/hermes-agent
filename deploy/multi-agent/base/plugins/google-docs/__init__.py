"""Google Docs plugin — READ-ONLY.

Registers into the ``google_docs`` toolset:
  * ``gdoc_read``     — read a Google Doc's text by URL/id (documents.readonly).
  * ``gdrive_search`` — full-text Drive search by content/name, TTL-cached
    (drive.readonly).

Credentials are resolved parent-side from a mounted read-only token /
service-account file (see _gauth). No write tools exist.
"""
from __future__ import annotations

from .tools import (
    GDOC_READ_SCHEMA,
    GDRIVE_SEARCH_SCHEMA,
    check_available,
    handle_gdoc_read,
    handle_gdrive_search,
)

_TOOLS = (
    ("gdoc_read", GDOC_READ_SCHEMA, handle_gdoc_read, "📄"),
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
