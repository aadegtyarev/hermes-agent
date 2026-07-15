"""SSH plugin — bounded but capable: one-shot, long/background sessions, and
key provisioning, over an allowlisted host set.

Tools (toolset `ssh`):
  ssh_run / ssh_read_file / ssh_list   — one-shot
  ssh_start / ssh_poll / ssh_send / ssh_stop / ssh_sessions — background sessions
  ssh_keygen / ssh_copy_id             — key generation + install

Host-key check is disabled by design (reflashed devices change host keys).
Opt-in via plugins.enabled: [ssh] and toolset `ssh`.
"""
from __future__ import annotations

import logging

from . import tools as T

logger = logging.getLogger(__name__)

_TOOLS = (
    ("ssh_run", T.SSH_RUN, T.handle_ssh_run, "🖥️"),
    ("ssh_read_file", T.SSH_READ_FILE, T.handle_ssh_read_file, "📄"),
    ("ssh_list", T.SSH_LIST, T.handle_ssh_list, "📁"),
    ("ssh_put", T.SSH_PUT, T.handle_ssh_put, "📥"),
    ("ssh_start", T.SSH_START, T.handle_ssh_start, "▶️"),
    ("ssh_poll", T.SSH_POLL, T.handle_ssh_poll, "📡"),
    ("ssh_send", T.SSH_SEND, T.handle_ssh_send, "⌨️"),
    ("ssh_stop", T.SSH_STOP, T.handle_ssh_stop, "⏹️"),
    ("ssh_sessions", T.SSH_SESSIONS, T.handle_ssh_sessions, "📋"),
    ("ssh_keygen", T.SSH_KEYGEN, T.handle_ssh_keygen, "🔑"),
    ("ssh_copy_id", T.SSH_COPY_ID, T.handle_ssh_copy_id, "📤"),
)

_AUDITED = {name for name, *_ in _TOOLS}


def _audit(tool_name, args, result, task_id, **kwargs):
    if tool_name in _AUDITED:
        logger.info("ssh tool %s host=%s (session %s)",
                    tool_name, (args or {}).get("host"), task_id)


def register(ctx) -> None:
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="ssh",
            schema=schema,
            handler=handler,
            check_fn=T.check_available,
            emoji=emoji,
        )
    ctx.register_hook("post_tool_call", _audit)
