"""YouTrack REST client (read + write).

Configuration is resolved PARENT-SIDE (gateway process): the permanent token
never reaches the model or the execute_code sandbox. Reads from env first,
then ``youtrack.*`` in config.yaml.

Write surface (comment, create_issue) is intentionally narrow; bound the blast
radius on the YouTrack side with a dedicated bot account / token scoped to the
projects it may touch.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

_HTTP: Optional[httpx.Client] = None

_ISSUE_FIELDS = (
    "idReadable,summary,description,created,updated,"
    "reporter(login,fullName),"
    "customFields(name,value(name,login,fullName,presentation)),"
    "comments(text,author(login,fullName),created)"
)


def _http() -> httpx.Client:
    global _HTTP
    if _HTTP is None:
        _HTTP = httpx.Client(timeout=30.0, follow_redirects=True)
    return _HTTP


def _val(env_name: str, section: str, key: str, default: str = "") -> str:
    import os

    raw = os.getenv(env_name)
    if raw and raw.strip():
        return raw.strip()
    try:
        from hermes_cli.config import cfg_get, load_config

        val = cfg_get(load_config(), section, key, default="")
        if val:
            return str(val).strip()
    except Exception:
        pass
    return default


def _base_url() -> str:
    return _val("YOUTRACK_URL", "youtrack", "url").rstrip("/")


def _token() -> str:
    return _val("YOUTRACK_TOKEN", "youtrack", "token")


def is_configured() -> bool:
    """True when both base URL and token are present (token required for the API)."""
    return bool(_base_url() and _token())


def _headers(write: bool = False) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/json",
    }
    if write:
        headers["Content-Type"] = "application/json"
    return headers


def _cf_value(value: Any) -> Any:
    if isinstance(value, dict):
        return (
            value.get("name")
            or value.get("fullName")
            or value.get("login")
            or value.get("presentation")
        )
    if isinstance(value, list):
        return [_cf_value(v) for v in value]
    return value


def _render_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    fields = {
        cf.get("name"): _cf_value(cf.get("value"))
        for cf in (issue.get("customFields") or [])
        if cf.get("name")
    }
    comments = [
        {
            "author": (c.get("author") or {}).get("login"),
            "created": c.get("created"),
            "text": c.get("text"),
        }
        for c in (issue.get("comments") or [])
    ]
    reporter = issue.get("reporter") or {}
    return {
        "id": issue.get("idReadable"),
        "summary": issue.get("summary"),
        "description": issue.get("description"),
        "reporter": reporter.get("login"),
        "fields": fields,
        "comments": comments,
    }


def search(query: str, max_results: int = 20) -> List[Dict[str, Any]]:
    base = _base_url()
    resp = _http().get(
        f"{base}/api/issues",
        params={"query": query, "fields": "idReadable,summary", "$top": max_results},
        headers=_headers(),
    )
    resp.raise_for_status()
    return [
        {"id": i.get("idReadable"), "summary": i.get("summary")}
        for i in (resp.json() or [])
    ]


def read_issue(issue_id: str) -> Dict[str, Any]:
    base = _base_url()
    resp = _http().get(
        f"{base}/api/issues/{issue_id}",
        params={"fields": _ISSUE_FIELDS},
        headers=_headers(),
    )
    resp.raise_for_status()
    return _render_issue(resp.json() or {})


def add_comment(issue_id: str, text: str) -> Dict[str, Any]:
    base = _base_url()
    resp = _http().post(
        f"{base}/api/issues/{issue_id}/comments",
        params={"fields": "id,text,created"},
        json={"text": text},
        headers=_headers(write=True),
    )
    resp.raise_for_status()
    body = resp.json() or {}
    return {"issue_id": issue_id, "comment_id": body.get("id"), "created": body.get("created")}


def _resolve_project_id(short_name: str) -> str:
    base = _base_url()
    resp = _http().get(
        f"{base}/api/admin/projects",
        params={"fields": "id,shortName", "query": short_name},
        headers=_headers(),
    )
    resp.raise_for_status()
    for proj in resp.json() or []:
        if str(proj.get("shortName")).lower() == short_name.lower():
            return proj.get("id")
    raise ValueError(f"project '{short_name}' not found (check shortName and token permissions)")


def create_issue(project: str, summary: str, description: str = "") -> Dict[str, Any]:
    base = _base_url()
    project_id = _resolve_project_id(project)
    resp = _http().post(
        f"{base}/api/issues",
        params={"fields": "idReadable"},
        json={
            "project": {"id": project_id},
            "summary": summary,
            "description": description,
        },
        headers=_headers(write=True),
    )
    resp.raise_for_status()
    body = resp.json() or {}
    return {"id": body.get("idReadable"), "url": f"{base}/issue/{body.get('idReadable')}"}
