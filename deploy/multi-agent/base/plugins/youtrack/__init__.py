"""youtrack plugin — YouTrack issue search, browse, comments, and creation.

Self-contained: talks to YouTrack REST API directly via requests.
Auth via permanent token from YOUTRACK_TOKEN env var.

Tools (toolset youtrack):
  yt_search        — search issues with flexible YouTrack query language
  yt_get_issue     — get full details of a single issue
  yt_list_projects — list available YouTrack projects
  yt_get_comments  — get comments for an issue
  yt_create_issue  — create an issue (unassigned) with an ai_auto tag
  yt_add_comment   — add a comment with the #ai-auto marker
  yt_work_items    — read logged time (spent time), auto-paged, per user & per ticket
"""

from __future__ import annotations

import logging

from .youtrack import (
    YT_SEARCH_SCHEMA,
    YT_GET_ISSUE_SCHEMA,
    YT_LIST_PROJECTS_SCHEMA,
    YT_GET_COMMENTS_SCHEMA,
    YT_CREATE_ISSUE_SCHEMA,
    YT_ADD_COMMENT_SCHEMA,
    YT_WORK_ITEMS_SCHEMA,
    handle_yt_search,
    handle_yt_get_issue,
    handle_yt_list_projects,
    handle_yt_get_comments,
    handle_yt_create_issue,
    handle_yt_add_comment,
    handle_yt_work_items,
    check_youtrack_deps,
)

logger = logging.getLogger(__name__)

_TOOLS = (
    ("yt_search",        YT_SEARCH_SCHEMA,        handle_yt_search,        "🔍"),
    ("yt_get_issue",     YT_GET_ISSUE_SCHEMA,     handle_yt_get_issue,     "📋"),
    ("yt_list_projects", YT_LIST_PROJECTS_SCHEMA, handle_yt_list_projects, "📁"),
    ("yt_get_comments",  YT_GET_COMMENTS_SCHEMA,  handle_yt_get_comments,  "💬"),
    ("yt_create_issue",  YT_CREATE_ISSUE_SCHEMA,  handle_yt_create_issue,  "✨"),
    ("yt_add_comment",   YT_ADD_COMMENT_SCHEMA,   handle_yt_add_comment,   "💭"),
    ("yt_work_items",    YT_WORK_ITEMS_SCHEMA,    handle_yt_work_items,    "⏱️"),
)


def register(ctx) -> None:
    """Register YouTrack tools. Called once by the plugin loader."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="youtrack",
            schema=schema,
            handler=handler,
            check_fn=check_youtrack_deps,
            emoji=emoji,
        )
    logger.info("youtrack plugin: registered %d tools", len(_TOOLS))
