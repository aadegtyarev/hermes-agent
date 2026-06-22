"""Discourse forum integration plugin (read-only).

Registers two tools into the ``discourse`` toolset:
  * ``discourse_search``     — search topics/posts
  * ``discourse_read_topic`` — read a full topic

The base URL (and optional API key) are read parent-side in the gateway
process; the key is never exposed to the model or the execute_code sandbox.
Opt-in via ``plugins.enabled: [discourse]`` and add ``discourse`` to a
platform's toolsets.
"""
from __future__ import annotations

from plugins.discourse.tools import (
    DISCOURSE_READ_TOPIC_SCHEMA,
    DISCOURSE_SEARCH_SCHEMA,
    check_discourse_available,
    handle_discourse_read_topic,
    handle_discourse_search,
)

_TOOLS = (
    ("discourse_search", DISCOURSE_SEARCH_SCHEMA, handle_discourse_search, "🔍"),
    ("discourse_read_topic", DISCOURSE_READ_TOPIC_SCHEMA, handle_discourse_read_topic, "📖"),
)


def register(ctx) -> None:
    """Register all Discourse tools. Called once by the plugin loader."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="discourse",
            schema=schema,
            handler=handler,
            check_fn=check_discourse_available,
            emoji=emoji,
        )
