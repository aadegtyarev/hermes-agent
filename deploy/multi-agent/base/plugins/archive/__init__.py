"""Archive plugin — archive_list / archive_extract / archive_create.

A clean tool surface for archives (zip/rar/7z/tar/…) so the agent doesn't hand-
roll code_execution scripts. Read/extract via lsar/unar; create via stdlib.
Opt-in via plugins.enabled: [archive] and toolset `archive`.
"""
from __future__ import annotations

from .tools import (
    ARCHIVE_CREATE,
    ARCHIVE_EXTRACT,
    ARCHIVE_LIST,
    handle_archive_create,
    handle_archive_extract,
    handle_archive_list,
)

_TOOLS = (
    ("archive_list", ARCHIVE_LIST, handle_archive_list, "📦"),
    ("archive_extract", ARCHIVE_EXTRACT, handle_archive_extract, "📂"),
    ("archive_create", ARCHIVE_CREATE, handle_archive_create, "🗜️"),
)


def register(ctx) -> None:
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="archive",
            schema=schema,
            handler=handler,
            emoji=emoji,
        )
