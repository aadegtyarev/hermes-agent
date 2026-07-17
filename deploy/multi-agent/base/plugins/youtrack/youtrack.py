"""YouTrack API plugin for Hermes — read-only issue search, browse, and comments.

Talks to YouTrack REST API directly via requests. Auth via permanent token.
Configuration via env vars:
  YOUTRACK_URL   — base URL, e.g. https://example.myjetbrains.com/youtrack
  YOUTRACK_TOKEN — permanent token (Bearer auth)
"""

from __future__ import annotations

import datetime
import json
import os
import urllib.parse

import requests


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

YOUTRACK_URL = os.environ.get("YOUTRACK_URL", "").rstrip("/")
YOUTRACK_TOKEN = os.environ.get("YOUTRACK_TOKEN", "")
# Operator's YouTrack login, used to resolve assignee='me' (the token may be
# authed under a different service user, so 'for: me' is unreliable). Kept in
# env, never hardcoded — instance-specific, not shipped in the skill.
YOUTRACK_LOGIN = os.environ.get("YOUTRACK_LOGIN", "").strip()
API_BASE = f"{YOUTRACK_URL}/api" if YOUTRACK_URL else ""


def _issue_url(issue_id: str) -> str:
    """Full web URL for an issue, built from YOUTRACK_URL (never hardcoded)."""
    return f"{YOUTRACK_URL}/issue/{issue_id}" if YOUTRACK_URL else ""


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _session() -> requests.Session:
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {YOUTRACK_TOKEN}"
    s.headers["Accept"] = "application/json"
    return s


def _get(path: str, params: dict | None = None, timeout: int = 30) -> dict:
    """GET request to YouTrack API, returns parsed JSON or error dict."""
    if not API_BASE:
        return {"error": "YOUTRACK_URL not configured"}
    if not YOUTRACK_TOKEN:
        return {"error": "YOUTRACK_TOKEN not configured"}
    url = f"{API_BASE}{path}"
    try:
        r = _session().get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        detail = ""
        try:
            detail = e.response.text[:500]
        except Exception:
            pass
        return {"error": f"HTTP {e.response.status_code}: {detail}"}
    except Exception as e:
        return {"error": str(e)}


def _post(path: str, json_body: dict | None = None, params: dict | None = None, timeout: int = 30) -> dict:
    """POST request to YouTrack API, returns parsed JSON or error dict."""
    if not API_BASE:
        return {"error": "YOUTRACK_URL not configured"}
    if not YOUTRACK_TOKEN:
        return {"error": "YOUTRACK_TOKEN not configured"}
    url = f"{API_BASE}{path}"
    try:
        r = _session().post(url, json=json_body, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        detail = ""
        try:
            detail = e.response.text[:500]
        except Exception:
            pass
        return {"error": f"HTTP {e.response.status_code}: {detail}"}
    except Exception as e:
        return {"error": str(e)}


def _ensure_tag(name: str) -> dict:
    """Find or create a tag by name. Returns dict with id and name."""
    # Search by name (faster than listing all tags)
    tags = _get("/tags", params={"fields": "id,name", "$top": 10, "query": name})
    if isinstance(tags, list):
        for t in tags:
            if t.get("name") == name:
                return {"id": t["id"], "name": name}
    # Tag not found — create it
    result = _post("/tags", json_body={"name": name}, params={"fields": "id,name"})
    if isinstance(result, dict) and result.get("id"):
        return {"id": result["id"], "name": name}
    return result  # error dict


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _err(msg: str, **extra) -> str:
    return _json({"success": False, "error": msg, **extra})


# ---------------------------------------------------------------------------
# Field sets for different views
# ---------------------------------------------------------------------------

ISSUE_FIELDS = (
    "idReadable,summary,description,"
    "project(name,shortName),"
    "reporter(fullName,login),"
    "assignee(fullName,login),"
    "created,updated,resolved,"
    "commentsCount,"
    "customFields(name,value(name,login,fullName,minutes,presentation,text,isResolved,id))"
)

ISSUE_BRIEF_FIELDS = (
    "idReadable,summary,"
    "project(shortName),"
    "reporter(fullName),"
    "assignee(fullName),"
    "created,updated,"
    "commentsCount"
)

COMMENT_FIELDS = "text,author(fullName,login),created,updated"
PROJECT_FIELDS = "id,name,shortName,description"
WORKITEM_FIELDS = (
    "id,date,duration(minutes,presentation),"
    "author(login,fullName),"
    "text,type(name),"
    "issue(idReadable,summary)"
)


# ---------------------------------------------------------------------------
# YouTrack query builder
# ---------------------------------------------------------------------------

def _build_query(
    query: str = "",
    project: str = "",
    assignee: str = "",
    text: str = "",
    in_comments: str = "",
    state: str = "",
) -> str:
    """Build a YouTrack search query from structured parameters."""
    parts = []
    if query.strip():
        parts.append(query.strip())
    if project.strip():
        parts.append(f"project: {{{project.strip()}}}")
    if assignee.strip():
        a = assignee.strip()
        if a.lower() in ("me", "self", "я"):
            # Resolve to the configured operator login when available; YouTrack's
            # 'for: me' binds to the token's user, which is often a service acct.
            if YOUTRACK_LOGIN:
                parts.append(f"assignee: {{{YOUTRACK_LOGIN}}}")
            else:
                parts.append("for: me")
        else:
            parts.append(f"assignee: {{{a}}}")
    if state.strip():
        parts.append(f"State: {{{state.strip()}}}")
    if text.strip():
        parts.append(text.strip())
    if in_comments.strip():
        parts.append(f"comments: {{{in_comments.strip()}}}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

YT_SEARCH_SCHEMA = {
    "name": "yt_search",
    "description": (
        "Search YouTrack issues. Use 'query' for a raw YouTrack query string, "
        "or the structured fields (project/assignee/text/state/in_comments) "
        "which will be combined into a proper query. At least one filter is required. "
        "Returns issue idReadable, summary, project, assignee, state, created, updated."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Raw YouTrack query string (e.g. '#FOO-123' or 'priority: Critical state: Open'). Overrides structured filters if both given."
            },
            "project": {
                "type": "string",
                "description": "Filter by project short name (e.g. 'FOO', 'BAR')"
            },
            "assignee": {
                "type": "string",
                "description": "Filter by assignee name or 'me' for current user"
            },
            "text": {
                "type": "string",
                "description": "Full-text search in issue summary and description"
            },
            "in_comments": {
                "type": "string",
                "description": "Full-text search in issue comments only"
            },
            "state": {
                "type": "string",
                "description": "Filter by state (e.g. 'Open', 'In Progress', 'Fixed')"
            },
            "max_results": {
                "type": "integer",
                "description": "Max results to return (default 20, max 100)"
            },
        },
        "required": [],
    },
}

YT_GET_ISSUE_SCHEMA = {
    "name": "yt_get_issue",
    "description": (
        "Get full details of a single YouTrack issue by its readable ID (e.g. 'FOO-123'). "
        "Returns summary, description, project, reporter, assignee, created, updated, "
        "resolved, comments count, and custom fields."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "issue_id": {
                "type": "string",
                "description": "Issue readable ID, e.g. 'FOO-123' or 'BAR-456'"
            },
        },
        "required": ["issue_id"],
    },
}

YT_LIST_PROJECTS_SCHEMA = {
    "name": "yt_list_projects",
    "description": (
        "List all YouTrack projects with their short names and IDs. "
        "Use this to discover project short names for use in yt_search."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional filter by project name substring"
            },
        },
        "required": [],
    },
}

YT_GET_COMMENTS_SCHEMA = {
    "name": "yt_get_comments",
    "description": (
        "Get comments for a YouTrack issue by its readable ID. "
        "Returns comment text, author, and timestamps."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "issue_id": {
                "type": "string",
                "description": "Issue readable ID, e.g. 'FOO-123'"
            },
            "max_results": {
                "type": "integer",
                "description": "Max comments to return (default 50, max 200)"
            },
        },
        "required": ["issue_id"],
    },
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_yt_search(args, **_kw) -> str:
    """Search issues in YouTrack."""
    query = args.get("query", "").strip()
    project = args.get("project", "").strip()
    assignee = args.get("assignee", "").strip()
    text = args.get("text", "").strip()
    in_comments = args.get("in_comments", "").strip()
    state = args.get("state", "").strip()
    max_results = min(int(args.get("max_results", 20) or 20), 100)

    if not query and not any([project, assignee, text, in_comments, state]):
        return _err(
            "At least one search parameter required. "
            "Use 'query' for raw YouTrack query, or structured fields: "
            "project, assignee, text, in_comments, state."
        )

    yt_query = _build_query(
        query=query,
        project=project,
        assignee=assignee,
        text=text,
        in_comments=in_comments,
        state=state,
    )
    params = {
        "query": yt_query,
        "fields": ISSUE_BRIEF_FIELDS,
        "$top": max_results,
    }
    result = _get("/issues", params=params)
    if "error" in result:
        return _err(result["error"])
    # Attach a ready-to-use web URL per issue so callers never hardcode the host.
    if isinstance(result, list):
        for it in result:
            if isinstance(it, dict) and it.get("idReadable"):
                it["url"] = _issue_url(it["idReadable"])
    return _json({
        "success": True,
        "you_track_query": yt_query,
        "count": len(result) if isinstance(result, list) else 0,
        "issues": result,
    })


def handle_yt_get_issue(args, **_kw) -> str:
    """Get full details of a single issue."""
    issue_id = args.get("issue_id", "").strip()
    if not issue_id:
        return _err("Missing required arg: 'issue_id' (e.g. 'FOO-123')")

    encoded_id = urllib.parse.quote(issue_id, safe="")
    params = {"fields": ISSUE_FIELDS}
    result = _get(f"/issues/{encoded_id}", params=params)
    if "error" in result:
        return _err(result["error"])
    if isinstance(result, dict) and result.get("idReadable"):
        result["url"] = _issue_url(result["idReadable"])
    return _json({"success": True, "issue": result})


def handle_yt_list_projects(args, **_kw) -> str:
    """List YouTrack projects."""
    query = args.get("query", "").strip()
    params = {
        "fields": PROJECT_FIELDS,
        "$top": 100,
    }
    if query:
        params["query"] = query
    result = _get("/admin/projects", params=params)
    if "error" in result:
        return _err(result["error"])
    return _json({"success": True, "count": len(result) if isinstance(result, list) else 0, "projects": result})


def handle_yt_get_comments(args, **_kw) -> str:
    """Get comments for an issue."""
    issue_id = args.get("issue_id", "").strip()
    if not issue_id:
        return _err("Missing required arg: 'issue_id' (e.g. 'FOO-123')")
    max_results = min(int(args.get("max_results", 50) or 50), 200)

    encoded_id = urllib.parse.quote(issue_id, safe="")
    params = {
        "fields": COMMENT_FIELDS,
        "$top": max_results,
    }
    result = _get(f"/issues/{encoded_id}/comments", params=params)
    if "error" in result:
        return _err(result["error"])
    return _json({"success": True, "issue_id": issue_id, "count": len(result) if isinstance(result, list) else 0, "comments": result})


YT_CREATE_ISSUE_SCHEMA = {
    "name": "yt_create_issue",
    "description": (
        "Create a new issue in YouTrack with the #ai_auto tag. "
        "Creates or reuses the 'ai_auto' tag automatically. "
        "Returns the new issue's readable ID and URL."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project": {
                "type": "string",
                "description": "Project short name, e.g. 'SOFT', 'DOC', 'INT'. Default: INT",
                "default": "INT"
            },
            "summary": {
                "type": "string",
                "description": "Issue summary (title)"
            },
            "description": {
                "type": "string",
                "description": "Optional issue description"
            },
            "tag": {
                "type": "string",
                "description": "Tag name to apply (default: ai_auto)",
                "default": "ai_auto"
            },
            "type": {
                "type": "string",
                "enum": ["Идея", "Bug", "Task"],
                "description": (
                    "Issue type. Omit to use the project's default type. "
                    "Available values: 'Идея', 'Bug' (ошибка), 'Task' (задание)."
                ),
            },
        },
        "required": ["summary"],
    },
}

YT_ADD_COMMENT_SCHEMA = {
    "name": "yt_add_comment",
    "description": (
        "Add a comment to an existing YouTrack issue. "
        "Automatically appends the #ai-auto marker to identify AI-generated comments."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "issue_id": {
                "type": "string",
                "description": "Issue readable ID, e.g. 'SOFT-123' or 'DOC-456'"
            },
            "text": {
                "type": "string",
                "description": "Comment text (will have #ai-auto appended)"
            },
        },
        "required": ["issue_id", "text"],
    },
}


# ---------------------------------------------------------------------------
# Create issue handler
# ---------------------------------------------------------------------------


def handle_yt_create_issue(args, **_kw) -> str:
    """Create a new issue with ai_auto tag."""
    project = args.get("project", "").strip() or "INT"
    summary = args.get("summary", "").strip()
    description = args.get("description", "").strip()
    # Empty/whitespace tag must not silently disable tagging — fall back to ai_auto.
    tag = (args.get("tag") or "ai_auto").strip() or "ai_auto"
    issue_type = (args.get("type") or "").strip()

    if not summary:
        return _err("Missing required arg: 'summary'")

    # Resolve project shortName -> id
    projs = _get("/admin/projects", params={"fields": "id,shortName", "$top": 200})
    if not isinstance(projs, list):
        return _err("Failed to list projects")

    proj_id = None
    proj_name_lower = project.lower()
    for p in projs:
        if p.get("shortName", "").lower() == proj_name_lower:
            proj_id = p["id"]
            break
    if not proj_id:
        return _err(f"Project '{project}' not found")

    # Ensure tag exists
    tag_result = _ensure_tag(tag)
    if "error" in tag_result:
        return _err(f"Failed to ensure tag '{tag}': {tag_result['error']}")

    # Create the issue. Explicitly clear Assignee so an auto-created ticket does
    # not land on the token owner via a project default / workflow. In this
    # instance Assignee is a MultiUserIssueCustomField, so "unassigned" is an
    # empty list (not null).
    custom_fields = [
        {
            "name": "Assignee",
            "$type": "MultiUserIssueCustomField",
            "value": [],
        }
    ]
    # Type is optional: when omitted, the project's default type applies.
    if issue_type:
        custom_fields.append({
            "name": "Type",
            "$type": "SingleEnumIssueCustomField",
            "value": {"name": issue_type},
        })

    body = {
        "summary": summary,
        "project": {"id": proj_id},
        "customFields": custom_fields,
    }
    if description:
        body["description"] = description

    result = _post("/issues", json_body=body, params={"fields": "idReadable,summary"})
    if "error" in result:
        msg = result["error"]
        if issue_type:
            # Most likely cause when a type was passed: an invalid enum value.
            msg += (
                f" (type='{issue_type}'? valid types: Идея, Bug, Task)"
            )
        return _err(msg)

    issue_id = result.get("idReadable", "?")
    issue_url = f"{YOUTRACK_URL}/issue/{issue_id}"

    # Apply tag. A failure here must surface loudly (not be swallowed), but still
    # report the created issue so the caller knows the ticket exists.
    if not tag_result.get("id"):
        return _err(
            f"Issue {issue_id} created, but tag '{tag}' could not be resolved "
            f"(no tag id).",
            issue_id=issue_id,
            url=issue_url,
        )

    tag_apply = _post(
        f"/issues/{issue_id}/tags",
        json_body={"id": tag_result["id"]},
        params={"fields": "id"},
    )
    if "error" in tag_apply:
        return _err(
            f"Issue {issue_id} created, but failed to apply tag '{tag}': "
            f"{tag_apply['error']}",
            issue_id=issue_id,
            url=issue_url,
        )

    return _json({
        "success": True,
        "issue_id": issue_id,
        "url": issue_url,
        "tag": tag,
        "type": issue_type or "(project default)",
    })


# ---------------------------------------------------------------------------
# Add comment handler
# ---------------------------------------------------------------------------


def handle_yt_add_comment(args, **_kw) -> str:
    """Add a comment to an existing issue with #ai-auto marker."""
    issue_id = args.get("issue_id", "").strip()
    text = args.get("text", "").strip()

    if not issue_id:
        return _err("Missing required arg: 'issue_id' (e.g. 'SOFT-123')")
    if not text:
        return _err("Missing required arg: 'text'")

    encoded_id = urllib.parse.quote(issue_id, safe="")
    comment_text = f"{text}\n\n#ai-auto"

    result = _post(
        f"/issues/{encoded_id}/comments",
        json_body={"text": comment_text},
        params={"fields": "id,text"},
    )
    if "error" in result:
        return _err(result["error"])

    return _json({
        "success": True,
        "issue_id": issue_id,
    })


# ---------------------------------------------------------------------------
# Work items (spent time) — READ-ONLY; aggregated per user AND per ticket
# ---------------------------------------------------------------------------

_WI_PAGE = 200          # page size for auto-paging
_WI_CEILING = 5000      # hard ceiling on total work items pulled in one call
_WI_ENTRIES_PREVIEW = 50


def _fmt_minutes(minutes: int) -> str:
    """Human 'Xч Yм' for a raw minute total (clock hours, not workday-based)."""
    minutes = int(minutes or 0)
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}ч {m}м"
    if h:
        return f"{h}ч"
    return f"{m}м"


def _wi_date_str(ms) -> str:
    """Epoch-ms work-item date -> 'YYYY-MM-DD' (UTC)."""
    try:
        return datetime.datetime.utcfromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _wi_author_matches(w: dict, needle_lower: str) -> bool:
    a = w.get("author") or {}
    return (
        needle_lower in (a.get("login") or "").lower()
        or needle_lower in (a.get("fullName") or "").lower()
    )


def _wi_in_range(w: dict, start: str, end: str) -> bool:
    """Keep a work item whose date falls in [start, end] ('YYYY-MM-DD', ISO-sortable).

    Used for the single-ticket endpoint, which has no server-side date filter.
    Undated items are dropped when any bound is set.
    """
    d = _wi_date_str(w.get("date"))
    if not d:
        return not (start or end)
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True


def _fetch_all_workitems(path: str, params: dict, ceiling: int):
    """Auto-page a work-items endpoint until exhausted or *ceiling* reached.

    Returns a (items, truncated) tuple, or an error dict if a page failed.
    """
    items: list = []
    skip = 0
    while len(items) < ceiling:
        page = dict(params)
        page["$top"] = min(_WI_PAGE, ceiling - len(items))
        page["$skip"] = skip
        batch = _get(path, params=page)
        if isinstance(batch, dict) and "error" in batch:
            return batch
        if not isinstance(batch, list):
            break
        items.extend(batch)
        if len(batch) < page["$top"]:
            return items, False
        skip += len(batch)
    return items, True  # hit the ceiling — more may exist


def _subtasks_of(parent_id: str):
    """Resolve a ticket's direct subtasks. Returns dict(parent, children) or error."""
    encoded = urllib.parse.quote(parent_id, safe="")
    res = _get(
        f"/issues/{encoded}",
        params={
            "fields": "idReadable,summary,"
            "links(direction,linkType(name,sourceToTarget),issues(idReadable,summary))"
        },
    )
    if isinstance(res, dict) and "error" in res:
        return res
    children = []
    for link in res.get("links") or []:
        lt = link.get("linkType") or {}
        name = (lt.get("name") or "").lower()
        s2t = (lt.get("sourceToTarget") or "").lower()
        # A parent points OUTWARD to its subtasks ("parent for" / "Subtask").
        if link.get("direction") == "OUTWARD" and ("subtask" in name or "parent" in s2t):
            for iss in link.get("issues") or []:
                if iss.get("idReadable"):
                    children.append({"id": iss["idReadable"], "summary": iss.get("summary", "")})
    return {
        "parent": {"id": res.get("idReadable", parent_id), "summary": res.get("summary", "")},
        "children": children,
    }


YT_WORK_ITEMS_SCHEMA = {
    "name": "yt_work_items",
    "description": (
        "Read logged time (work items / spent time) and get it broken down BY USER "
        "and BY TICKET. Read-only, auto-paged (fetches everything in the scope, no "
        "manual paging). Pick ONE scope:\n"
        "  • issue_id='FOO-123' — time logged on that single ticket (by_user then "
        "answers 'who spent how much on it'); add start_date/end_date to limit to a period.\n"
        "  • subtasks_of='FOO-123' — time per SUBTASK of that ticket (plus the "
        "parent's own time); subtasks with no logged time are listed as 0.\n"
        "  • project='FOO' and/or query=<YouTrack issue query>, optionally with a "
        "start_date..end_date range — a cross-ticket report (e.g. time per ticket "
        "and per person on a project last month).\n"
        "Always returns by_user (per person) and by_issue (per ticket), the grand "
        "total, and a sample of raw entries. Optional author= filter. An unbounded "
        "fetch (no issue_id, no subtasks_of, no project/query, no dates) is refused."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "issue_id": {
                "type": "string",
                "description": "Single ticket's own time log, e.g. 'FOO-123'.",
            },
            "subtasks_of": {
                "type": "string",
                "description": "Parent ticket id, e.g. 'FOO-123' — reports time per subtask (and the parent).",
            },
            "project": {
                "type": "string",
                "description": "Project short name to scope a cross-ticket report (e.g. 'FOO').",
            },
            "query": {
                "type": "string",
                "description": "Raw YouTrack issue query to scope a cross-ticket report (combined with 'project').",
            },
            "author": {
                "type": "string",
                "description": "Restrict to one user (login works best; full name also matched).",
            },
            "start_date": {
                "type": "string",
                "description": "Period start 'YYYY-MM-DD' (inclusive). Applies to every scope, including a single issue_id.",
            },
            "end_date": {
                "type": "string",
                "description": "Period end 'YYYY-MM-DD' (inclusive). Applies to every scope, including a single issue_id.",
            },
            "max_results": {
                "type": "integer",
                "description": f"Hard cap on work items pulled (default/max {_WI_CEILING}). Lower it to sample faster.",
            },
        },
        "required": [],
    },
}


def handle_yt_work_items(args, **_kw) -> str:
    """Fetch work items (spent time), auto-paged, aggregated per user AND per ticket."""
    issue_id = args.get("issue_id", "").strip()
    subtasks_of = args.get("subtasks_of", "").strip()
    query = args.get("query", "").strip()
    project = args.get("project", "").strip()
    author = args.get("author", "").strip()
    start_date = args.get("start_date", "").strip()
    end_date = args.get("end_date", "").strip()
    try:
        ceiling = int(args.get("max_results") or _WI_CEILING)
    except (TypeError, ValueError):
        ceiling = _WI_CEILING
    ceiling = max(1, min(ceiling, _WI_CEILING))

    # issues seeded into by_issue at 0 min so complete sets (subtasks) show gaps
    seeded_issues: dict = {}
    client_author_filter = False

    if issue_id:
        path = f"/issues/{urllib.parse.quote(issue_id, safe='')}/timeTracking/workItems"
        params = {"fields": WORKITEM_FIELDS}
        # this endpoint has no server-side author or date filter — apply both client-side
        client_author_filter = bool(author)
        scope = {"issue_id": issue_id}
        if author:
            scope["author"] = author
        if start_date:
            scope["start_date"] = start_date
        if end_date:
            scope["end_date"] = end_date
    elif subtasks_of:
        info = _subtasks_of(subtasks_of)
        if isinstance(info, dict) and "error" in info:
            return _err(info["error"])
        parent, children = info["parent"], info["children"]
        ids = [parent["id"]] + [c["id"] for c in children]
        seeded_issues = {parent["id"]: parent["summary"], **{c["id"]: c["summary"] for c in children}}
        path = "/workItems"
        params = {"fields": WORKITEM_FIELDS, "query": "issue id: " + ", ".join(ids)}
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date
        if author:
            params["author"] = author
        scope = {"subtasks_of": parent["id"], "subtasks": len(children)}
    else:
        q = query
        if project:
            q = (q + " " if q else "") + f"project: {{{project}}}"
        q = q.strip()
        if not q and not (start_date or end_date):
            return _err(
                "Provide 'issue_id', 'subtasks_of', or scope a cross-ticket report "
                "with 'project'/'query' and/or 'start_date'..'end_date'. "
                "Refusing an unbounded work-items fetch."
            )
        path = "/workItems"
        params = {"fields": WORKITEM_FIELDS}
        if q:
            params["query"] = q
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date
        if author:
            params["author"] = author
        scope = {
            k: v
            for k, v in {
                "project": project,
                "query": query,
                "author": author,
                "start_date": start_date,
                "end_date": end_date,
            }.items()
            if v
        }

    fetched = _fetch_all_workitems(path, params, ceiling)
    if isinstance(fetched, dict) and "error" in fetched:
        return _err(fetched["error"])
    items, truncated = fetched

    if client_author_filter:
        items = [w for w in items if _wi_author_matches(w, author.lower())]
    # single-ticket endpoint can't filter by date server-side — do it here
    if issue_id and (start_date or end_date):
        items = [w for w in items if _wi_in_range(w, start_date, end_date)]

    by_user: dict = {}
    by_issue: dict = {
        iid: {"issue": iid, "summary": summ, "minutes": 0, "entries": 0}
        for iid, summ in seeded_issues.items()
    }
    total = 0
    entries = []
    for w in items:
        dur = w.get("duration") or {}
        mins = int(dur.get("minutes") or 0)
        total += mins
        a = w.get("author") or {}
        ukey = a.get("login") or a.get("fullName") or "(unknown)"
        u = by_user.setdefault(
            ukey,
            {"author": a.get("fullName") or ukey, "login": a.get("login") or "", "minutes": 0, "entries": 0},
        )
        u["minutes"] += mins
        u["entries"] += 1
        iss = w.get("issue") or {}
        ikey = iss.get("idReadable") or issue_id or "(unknown)"
        i = by_issue.setdefault(
            ikey, {"issue": ikey, "summary": iss.get("summary", ""), "minutes": 0, "entries": 0}
        )
        if iss.get("summary") and not i.get("summary"):
            i["summary"] = iss["summary"]
        i["minutes"] += mins
        i["entries"] += 1
        entries.append(
            {
                "issue": ikey,
                "date": _wi_date_str(w.get("date")),
                "author": a.get("fullName") or a.get("login") or "(unknown)",
                "minutes": mins,
                "duration": _fmt_minutes(mins),
                "type": (w.get("type") or {}).get("name", ""),
                "text": (w.get("text") or "")[:200],
            }
        )

    users = sorted(by_user.values(), key=lambda x: -x["minutes"])
    for u in users:
        u["duration"] = _fmt_minutes(u["minutes"])
    issues = sorted(by_issue.values(), key=lambda x: -x["minutes"])
    for i in issues:
        i["duration"] = _fmt_minutes(i["minutes"])

    return _json(
        {
            "success": True,
            "scope": scope,
            "count": len(items),
            "truncated": truncated,
            "total_minutes": total,
            "total": _fmt_minutes(total),
            "by_user": users,
            "by_issue": issues,
            "entries": entries[:_WI_ENTRIES_PREVIEW],
            "entries_truncated": len(entries) > _WI_ENTRIES_PREVIEW,
            "note": (
                "Durations shown as Russian ч/м (clock hours summed from minutes, "
                "not workday-based); use total_minutes/minutes for math. "
                "Results are auto-paged; 'truncated' is true only if the "
                f"{_WI_CEILING}-item ceiling was hit."
            ),
        }
    )


# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

def check_youtrack_deps() -> bool:
    """Tool availability gate: YOUTRACK_URL and YOUTRACK_TOKEN must be set."""
    if not YOUTRACK_URL:
        return False
    if not YOUTRACK_TOKEN:
        return False
    try:
        import requests  # noqa: F401
        return True
    except ImportError:
        return False
