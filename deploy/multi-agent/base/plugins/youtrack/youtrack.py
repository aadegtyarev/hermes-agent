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
# Work items (spent time) — READ-ONLY, aggregated per user
# ---------------------------------------------------------------------------

def _fmt_minutes(minutes: int) -> str:
    """Human 'Xh Ym' for a raw minute total (clock hours, not workday-based)."""
    minutes = int(minutes or 0)
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


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


YT_WORK_ITEMS_SCHEMA = {
    "name": "yt_work_items",
    "description": (
        "Read logged time (work items) and get spent time broken down PER USER. "
        "Read-only. Two modes: (1) pass 'issue_id' for one ticket's time log; "
        "(2) omit it and scope with 'project'/'query' and/or a 'start_date'..'end_date' "
        "range to report across many tickets (e.g. who spent how much on a project "
        "last month). Returns per-user totals (by_user), the grand total, and a "
        "sample of individual entries. An unbounded fetch (no issue_id, no scope, "
        "no dates) is refused."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "issue_id": {
                "type": "string",
                "description": "Single issue readable ID (e.g. 'FOO-123'); returns just that ticket's work items.",
            },
            "project": {
                "type": "string",
                "description": "Project short name to scope a cross-issue report (e.g. 'FOO').",
            },
            "query": {
                "type": "string",
                "description": "Raw YouTrack issue query to scope a cross-issue report (combined with 'project').",
            },
            "author": {
                "type": "string",
                "description": "Restrict to one user (login works best; full name also matched).",
            },
            "start_date": {
                "type": "string",
                "description": "Report period start, 'YYYY-MM-DD' (inclusive). Cross-issue mode only.",
            },
            "end_date": {
                "type": "string",
                "description": "Report period end, 'YYYY-MM-DD' (inclusive). Cross-issue mode only.",
            },
            "max_results": {
                "type": "integer",
                "description": "Max work items to fetch/aggregate (default 500, max 2000).",
            },
        },
        "required": [],
    },
}

_WI_ENTRIES_PREVIEW = 50


def handle_yt_work_items(args, **_kw) -> str:
    """Fetch work items (spent time) and aggregate per user. Read-only."""
    issue_id = args.get("issue_id", "").strip()
    query = args.get("query", "").strip()
    project = args.get("project", "").strip()
    author = args.get("author", "").strip()
    start_date = args.get("start_date", "").strip()
    end_date = args.get("end_date", "").strip()
    max_results = min(int(args.get("max_results", 500) or 500), 2000)

    if issue_id:
        encoded_id = urllib.parse.quote(issue_id, safe="")
        result = _get(
            f"/issues/{encoded_id}/timeTracking/workItems",
            params={"fields": WORKITEM_FIELDS, "$top": max_results},
        )
        scope = {"issue_id": issue_id}
        if author:
            scope["author"] = author
    else:
        q = query
        if project:
            q = (q + " " if q else "") + f"project: {{{project}}}"
        q = q.strip()
        if not q and not (start_date or end_date):
            return _err(
                "Provide 'issue_id', or scope a cross-issue report with "
                "'project'/'query' and/or 'start_date'..'end_date'. "
                "Refusing an unbounded work-items fetch."
            )
        params = {"fields": WORKITEM_FIELDS, "$top": max_results}
        if q:
            params["query"] = q
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date
        if author:
            params["author"] = author
        result = _get("/workItems", params=params)
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

    if isinstance(result, dict) and "error" in result:
        return _err(result["error"])
    items = result if isinstance(result, list) else []

    # Per-issue endpoint has no server-side author filter — apply it client-side.
    if author and issue_id:
        items = [w for w in items if _wi_author_matches(w, author.lower())]

    by: dict = {}
    total = 0
    entries = []
    for w in items:
        dur = w.get("duration") or {}
        mins = int(dur.get("minutes") or 0)
        total += mins
        a = w.get("author") or {}
        key = a.get("login") or a.get("fullName") or "(unknown)"
        b = by.setdefault(
            key,
            {"author": a.get("fullName") or key, "login": a.get("login") or "", "minutes": 0, "entries": 0},
        )
        b["minutes"] += mins
        b["entries"] += 1
        iss = w.get("issue") or {}
        entries.append(
            {
                "issue": iss.get("idReadable", issue_id),
                "date": _wi_date_str(w.get("date")),
                "author": a.get("fullName") or a.get("login") or "(unknown)",
                "minutes": mins,
                "duration": (dur.get("presentation") or _fmt_minutes(mins)),
                "type": (w.get("type") or {}).get("name", ""),
                "text": (w.get("text") or "")[:200],
            }
        )

    by_user = sorted(by.values(), key=lambda x: -x["minutes"])
    for b in by_user:
        b["duration"] = _fmt_minutes(b["minutes"])

    return _json(
        {
            "success": True,
            "scope": scope,
            "count": len(items),
            "total_minutes": total,
            "total": _fmt_minutes(total),
            "by_user": by_user,
            "entries": entries[:_WI_ENTRIES_PREVIEW],
            "entries_truncated": len(entries) > _WI_ENTRIES_PREVIEW,
            "note": (
                "Durations are clock h/m summed from minutes, not workday-based. "
                "count is capped by max_results; raise it if truncated."
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
