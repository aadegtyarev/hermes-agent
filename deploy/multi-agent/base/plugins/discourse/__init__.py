"""discourse plugin — read-only Discourse search, thread reading, and
attachment/image extraction.

Self-contained: talks to the Discourse REST API directly via requests.
Auth via optional Api-Key/Api-Username pair (DISCOURSE_API_KEY /
DISCOURSE_API_USERNAME env vars) — falls back to anonymous access if unset.

Read-only by design — no reply, post, like, edit, or delete tool exists in
this plugin. Combine with a Discourse API key scoped "Read Only" (see
README.md) so the restriction holds even if the model is fed instructions
from within forum content (prompt injection from an untrusted thread).

Tools (toolset discourse):
  discourse_search           — flexible search (free text + structured filters)
  discourse_get_topic        — read a full thread, paginated
  discourse_list_latest      — recently active topics, optionally by category
  discourse_list_categories  — discover category slugs/ids
  discourse_get_attachments  — list or download images/attachments from a topic
"""

from __future__ import annotations

import logging

from .discourse import (
    DISCOURSE_SEARCH_SCHEMA,
    DISCOURSE_GET_TOPIC_SCHEMA,
    DISCOURSE_LIST_LATEST_SCHEMA,
    DISCOURSE_LIST_CATEGORIES_SCHEMA,
    DISCOURSE_GET_ATTACHMENTS_SCHEMA,
    handle_discourse_search,
    handle_discourse_get_topic,
    handle_discourse_list_latest,
    handle_discourse_list_categories,
    handle_discourse_get_attachments,
    check_discourse_deps,
)

logger = logging.getLogger(__name__)

_TOOLS = (
    ("discourse_search",           DISCOURSE_SEARCH_SCHEMA,           handle_discourse_search,           "🔎"),
    ("discourse_get_topic",        DISCOURSE_GET_TOPIC_SCHEMA,        handle_discourse_get_topic,        "🧵"),
    ("discourse_list_latest",      DISCOURSE_LIST_LATEST_SCHEMA,      handle_discourse_list_latest,      "🕘"),
    ("discourse_list_categories",  DISCOURSE_LIST_CATEGORIES_SCHEMA,  handle_discourse_list_categories,  "📂"),
    ("discourse_get_attachments",  DISCOURSE_GET_ATTACHMENTS_SCHEMA,  handle_discourse_get_attachments,  "📎"),
)


def register(ctx) -> None:
    """Register discourse tools. Called once by the plugin loader."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="discourse",
            schema=schema,
            handler=handler,
            check_fn=check_discourse_deps,
            emoji=emoji,
        )
    logger.info("discourse plugin: registered %d tools", len(_TOOLS))
