"""YouTrack tool schemas + handlers (read + write).

Handlers run parent-side and return JSON strings via tool_result/tool_error.
Write tools (comment, create_issue) act under the configured token — bound its
permissions on the YouTrack side.
"""
from __future__ import annotations

from tools.registry import tool_error, tool_result

from plugins.youtrack import client


def check_youtrack_available() -> bool:
    """Gate: tools are usable once base URL + token are configured."""
    try:
        return client.is_configured()
    except Exception:
        return False


YOUTRACK_SEARCH_SCHEMA = {
    "name": "youtrack_search",
    "description": (
        "Search YouTrack issues using YouTrack query language (e.g. "
        "'project: SUP State: Open assignee: me'). Read-only. Returns matching "
        "issue ids and summaries; use youtrack_read_issue for full detail."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "YouTrack query (YQL)."},
            "max_results": {
                "type": "integer",
                "description": "Maximum issues to return (default 20).",
                "default": 20,
            },
        },
        "required": ["query"],
    },
}

YOUTRACK_READ_ISSUE_SCHEMA = {
    "name": "youtrack_read_issue",
    "description": (
        "Read a YouTrack issue by its readable id (e.g. 'SUP-1234'), including "
        "summary, description, custom fields and comments. Read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "issue_id": {"type": "string", "description": "Readable issue id, e.g. 'SUP-1234'."},
        },
        "required": ["issue_id"],
    },
}

YOUTRACK_COMMENT_SCHEMA = {
    "name": "youtrack_comment",
    "description": (
        "Add a comment to a YouTrack issue. WRITE action — posts under the "
        "configured bot token. Use only when explicitly asked to comment."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "issue_id": {"type": "string", "description": "Readable issue id, e.g. 'SUP-1234'."},
            "text": {"type": "string", "description": "Comment text (Markdown supported)."},
        },
        "required": ["issue_id", "text"],
    },
}

YOUTRACK_CREATE_ISSUE_SCHEMA = {
    "name": "youtrack_create_issue",
    "description": (
        "Create a new YouTrack issue in a project. WRITE action — created under "
        "the configured bot token. Use only when explicitly asked to create an issue."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Project shortName, e.g. 'SUP'."},
            "summary": {"type": "string", "description": "Issue summary / title."},
            "description": {
                "type": "string",
                "description": "Issue description (Markdown supported).",
                "default": "",
            },
        },
        "required": ["project", "summary"],
    },
}


def handle_youtrack_search(args: dict, **kw) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return tool_error("query is required")
    try:
        max_results = int(args.get("max_results", 20))
    except (TypeError, ValueError):
        max_results = 20
    try:
        results = client.search(query, max_results=max_results)
        return tool_result({"query": query, "count": len(results), "results": results})
    except Exception as e:
        return tool_error(f"YouTrack search failed: {e}")


def handle_youtrack_read_issue(args: dict, **kw) -> str:
    issue_id = str(args.get("issue_id") or "").strip()
    if not issue_id:
        return tool_error("issue_id is required")
    try:
        return tool_result(client.read_issue(issue_id))
    except Exception as e:
        return tool_error(f"Failed to read YouTrack issue {issue_id}: {e}")


def handle_youtrack_comment(args: dict, **kw) -> str:
    issue_id = str(args.get("issue_id") or "").strip()
    text = str(args.get("text") or "").strip()
    if not issue_id:
        return tool_error("issue_id is required")
    if not text:
        return tool_error("text is required")
    try:
        return tool_result(client.add_comment(issue_id, text))
    except Exception as e:
        return tool_error(f"Failed to comment on YouTrack issue {issue_id}: {e}")


def handle_youtrack_create_issue(args: dict, **kw) -> str:
    project = str(args.get("project") or "").strip()
    summary = str(args.get("summary") or "").strip()
    description = str(args.get("description") or "")
    if not project:
        return tool_error("project is required")
    if not summary:
        return tool_error("summary is required")
    try:
        return tool_result(client.create_issue(project, summary, description))
    except Exception as e:
        return tool_error(f"Failed to create YouTrack issue: {e}")
