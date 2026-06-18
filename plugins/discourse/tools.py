"""Discourse tool schemas + handlers (read-only).

Handlers run parent-side and return JSON strings via tool_result/tool_error.
"""
from __future__ import annotations

from tools.registry import tool_error, tool_result

from plugins.discourse import client


def check_discourse_available() -> bool:
    """Gate: tools are usable once a Discourse base URL is configured."""
    try:
        return client.is_configured()
    except Exception:
        return False


DISCOURSE_SEARCH_SCHEMA = {
    "name": "discourse_search",
    "description": (
        "Search the team's Discourse forum for topics and posts matching a "
        "query. Read-only. Returns matching topics with id, title, url and a "
        "short blurb. Use discourse_read_topic to read a full thread."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (Discourse search syntax supported, e.g. 'modbus category:support').",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default 10).",
                "default": 10,
            },
        },
        "required": ["query"],
    },
}


DISCOURSE_READ_TOPIC_SCHEMA = {
    "name": "discourse_read_topic",
    "description": (
        "Read a Discourse topic by its numeric id, including its posts "
        "(text, author, timestamp). Read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic_id": {
                "type": "string",
                "description": "Numeric topic id (from discourse_search results or a topic URL /t/<id>).",
            },
            "post_limit": {
                "type": "integer",
                "description": "Maximum number of posts to return (default 20).",
                "default": 20,
            },
        },
        "required": ["topic_id"],
    },
}


def handle_discourse_search(args: dict, **kw) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return tool_error("query is required")
    try:
        max_results = int(args.get("max_results", 10))
    except (TypeError, ValueError):
        max_results = 10
    try:
        results = client.search(query, max_results=max_results)
        return tool_result({"query": query, "count": len(results), "results": results})
    except Exception as e:
        return tool_error(f"Discourse search failed: {e}")


def handle_discourse_read_topic(args: dict, **kw) -> str:
    topic_id = str(args.get("topic_id") or "").strip()
    if not topic_id:
        return tool_error("topic_id is required")
    try:
        post_limit = int(args.get("post_limit", 20))
    except (TypeError, ValueError):
        post_limit = 20
    try:
        return tool_result(client.read_topic(topic_id, post_limit=post_limit))
    except Exception as e:
        return tool_error(f"Failed to read Discourse topic {topic_id}: {e}")
