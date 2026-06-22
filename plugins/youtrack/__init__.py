"""YouTrack issue-tracker integration plugin (read + write).

Registers four tools into the ``youtrack`` toolset:
  * ``youtrack_search``       — search issues (YQL)        [read]
  * ``youtrack_read_issue``   — read an issue + comments   [read]
  * ``youtrack_comment``      — add a comment              [write]
  * ``youtrack_create_issue`` — create an issue            [write]

Base URL and permanent token are read parent-side in the gateway process; the
token is never exposed to the model or the execute_code sandbox. Write tools
act under that token — scope a dedicated bot account to the projects it may
touch. Opt-in via ``plugins.enabled: [youtrack]`` and add ``youtrack`` to a
platform's toolsets.
"""
from __future__ import annotations

from plugins.youtrack.tools import (
    YOUTRACK_COMMENT_SCHEMA,
    YOUTRACK_CREATE_ISSUE_SCHEMA,
    YOUTRACK_READ_ISSUE_SCHEMA,
    YOUTRACK_SEARCH_SCHEMA,
    check_youtrack_available,
    handle_youtrack_comment,
    handle_youtrack_create_issue,
    handle_youtrack_read_issue,
    handle_youtrack_search,
)

_TOOLS = (
    ("youtrack_search", YOUTRACK_SEARCH_SCHEMA, handle_youtrack_search, "🔍"),
    ("youtrack_read_issue", YOUTRACK_READ_ISSUE_SCHEMA, handle_youtrack_read_issue, "📋"),
    ("youtrack_comment", YOUTRACK_COMMENT_SCHEMA, handle_youtrack_comment, "💬"),
    ("youtrack_create_issue", YOUTRACK_CREATE_ISSUE_SCHEMA, handle_youtrack_create_issue, "🆕"),
)


def register(ctx) -> None:
    """Register all YouTrack tools. Called once by the plugin loader."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="youtrack",
            schema=schema,
            handler=handler,
            check_fn=check_youtrack_available,
            emoji=emoji,
        )
