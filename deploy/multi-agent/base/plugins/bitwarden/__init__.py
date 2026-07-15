"""Bitwarden vault plugin — vault_list / vault_get / vault_field / vault_totp.

A bounded retrieval surface over the `bw` CLI: the agent fetches a specific
secret on demand (audit-logged) rather than holding every credential in env.
Vault creds (BW_*) are env vars — scrubbed from the code_execution sandbox.
Opt-in via plugins.enabled: [bitwarden] and toolset `vault`.
"""
from __future__ import annotations

import logging

from . import tools as T

logger = logging.getLogger(__name__)

_TOOLS = (
    ("vault_list", T.VAULT_LIST, T.handle_vault_list, "🔎"),
    ("vault_get", T.VAULT_GET, T.handle_vault_get, "🔐"),
    ("vault_field", T.VAULT_FIELD, T.handle_vault_field, "🔑"),
    ("vault_totp", T.VAULT_TOTP, T.handle_vault_totp, "⏱️"),
)

_AUDITED = {name for name, *_ in _TOOLS}


def _audit(tool_name, args, result, task_id, **kwargs):
    # Log the ACCESS (tool + item name), never the returned secret value.
    if tool_name in _AUDITED:
        logger.info("vault access %s item=%s (session %s)",
                    tool_name, (args or {}).get("name") or (args or {}).get("search"), task_id)


def register(ctx) -> None:
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="vault",
            schema=schema,
            handler=handler,
            check_fn=T.check_available,
            emoji=emoji,
        )
    ctx.register_hook("post_tool_call", _audit)
