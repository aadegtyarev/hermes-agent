"""GitHub plugin — registration."""

import logging

from . import schemas, tools

logger = logging.getLogger(__name__)
_TOOLSET = "github"


def _on_post_tool_call(tool_name, args, result, task_id, **kwargs):
    """Log GitHub tool calls for audit trail."""
    if tool_name.startswith("github_"):
        logger.debug("GitHub tool %s (session %s): %s", tool_name, task_id,
                     str(args)[:200])


def _log_terminal_github_use(tool_name, args, task_id, **kwargs):
    """Log (don't block) terminal commands that touch GitHub directly.

    The github_* tools are the preferred path — they enforce the repo
    allowlist and give structured output — but they depend on `gh` being
    authenticated / GITHUB_TOKEN being set. When neither is configured,
    the agent has no working GitHub path at all unless it can fall back
    to terminal/gh/git/curl, so this only records the fallback for the
    audit trail instead of raising.
    """
    if tool_name != "terminal":
        return
    cmd = args.get("command", "")
    if not isinstance(cmd, str):
        return
    if tools._GH_BYPASS_RE.search(cmd):
        logger.debug("Terminal GitHub fallback used (task %s): %s", task_id, cmd[:100])


def register(ctx):
    """Register all GitHub tools and hooks."""
    ctx.register_tool(
        name="github_issue_list",
        toolset=_TOOLSET,
        schema=schemas.ISSUE_LIST,
        handler=tools.issue_list,
    )
    ctx.register_tool(
        name="github_issue_view",
        toolset=_TOOLSET,
        schema=schemas.ISSUE_VIEW,
        handler=tools.issue_view,
    )
    ctx.register_tool(
        name="github_issue_create",
        toolset=_TOOLSET,
        schema=schemas.ISSUE_CREATE,
        handler=tools.issue_create,
    )
    ctx.register_tool(
        name="github_pr_list",
        toolset=_TOOLSET,
        schema=schemas.PR_LIST,
        handler=tools.pr_list,
    )
    ctx.register_tool(
        name="github_pr_view",
        toolset=_TOOLSET,
        schema=schemas.PR_VIEW,
        handler=tools.pr_view,
    )
    ctx.register_tool(
        name="github_pr_merge",
        toolset=_TOOLSET,
        schema=schemas.PR_MERGE,
        handler=tools.pr_merge,
    )
    ctx.register_tool(
        name="github_repo_search",
        toolset=_TOOLSET,
        schema=schemas.REPO_SEARCH,
        handler=tools.repo_search,
    )
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("pre_tool_call", _log_terminal_github_use)
