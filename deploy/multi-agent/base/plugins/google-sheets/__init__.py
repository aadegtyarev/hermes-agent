"""Google Sheets plugin — READ-ONLY.

Registers two tools into the ``google_sheets`` toolset:
  * ``gsheet_list_sheets`` — list tabs/dimensions (spreadsheets.readonly).
  * ``gsheet_read``        — read cell values by range (spreadsheets.readonly).

Credentials are resolved parent-side from a mounted read-only token /
service-account file (see _gauth). No write tools exist.
"""
from __future__ import annotations

from .tools import (
    GSHEET_LIST_SHEETS_SCHEMA,
    GSHEET_READ_SCHEMA,
    check_available,
    handle_gsheet_list_sheets,
    handle_gsheet_read,
)

_TOOLS = (
    ("gsheet_list_sheets", GSHEET_LIST_SHEETS_SCHEMA, handle_gsheet_list_sheets, "📑"),
    ("gsheet_read", GSHEET_READ_SCHEMA, handle_gsheet_read, "📊"),
)


def register(ctx) -> None:
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="google_sheets",
            schema=schema,
            handler=handler,
            check_fn=check_available,
            emoji=emoji,
        )
