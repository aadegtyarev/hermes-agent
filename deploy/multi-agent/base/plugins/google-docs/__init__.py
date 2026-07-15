"""Google Docs plugin — READ-ONLY.

Registers one tool into the ``google_docs`` toolset:
  * ``gdoc_read`` — read a Google Doc's text by URL/id (documents.readonly).

Credentials are resolved parent-side from a mounted read-only token /
service-account file (see _gauth). No write tools exist.
"""
from __future__ import annotations

from .tools import GDOC_READ_SCHEMA, check_available, handle_gdoc_read


def register(ctx) -> None:
    ctx.register_tool(
        name="gdoc_read",
        toolset="google_docs",
        schema=GDOC_READ_SCHEMA,
        handler=handle_gdoc_read,
        check_fn=check_available,
        emoji="📄",
    )
