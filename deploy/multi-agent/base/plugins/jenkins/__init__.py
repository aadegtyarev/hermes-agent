"""jenkins plugin — Jenkins CI tools (job / build / log / trigger / search / list).

Config is read **parent-side** from env, so the token never reaches the model or
the `code_execution` sandbox (which scrubs TOKEN/KEY/…):
  JENKINS_URL    — base URL, e.g. https://jenkins.wirenboard.com
  JENKINS_USER   — user email (optional; enables Pattern A)
  JENKINS_TOKEN  — API token

Auth (Basic):
  JENKINS_USER + JENKINS_TOKEN → ``user:token``  (Pattern A — matrix-auth, WB Jenkins)
  JENKINS_TOKEN only           → ``token:``       (Pattern B — legacy token-as-user)

Tools register into the ``jenkins`` toolset. Opt in via plugins.enabled: [jenkins]
and add ``jenkins`` to the platform toolsets.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime

from tools.registry import tool_error, tool_result

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config / auth (parent-side; env only)
# ---------------------------------------------------------------------------

def _auth():
    """Return ((base_url, basic_cred), "") or (None, error_message)."""
    url = os.environ.get("JENKINS_URL", "").rstrip("/")
    user = os.environ.get("JENKINS_USER", "").strip()
    token = os.environ.get("JENKINS_TOKEN", "").strip()
    if not url:
        return None, "JENKINS_URL not set"
    if not token:
        return None, "JENKINS_TOKEN not set"
    cred = f"{user}:{token}" if user else f"{token}:"  # Pattern A if user, else B
    return (url, cred), ""


def _is_configured() -> bool:
    cfg, _ = _auth()
    return cfg is not None


def _basic(cred: str) -> str:
    return base64.b64encode(cred.encode()).decode()


def _build_job_path(folder: str = "", job: str = "", branch: str = "") -> str:
    parts = []
    for seg_group in (folder, job, branch):
        if seg_group:
            for seg in seg_group.strip("/").split("/"):
                parts.append(f"job/{urllib.request.quote(seg, safe='')}")
    return "/" + "/".join(parts)


def _get(url: str, path: str, cred: str, tree: str | None = None) -> dict:
    query = f"?tree={urllib.request.quote(tree)}" if tree else ""
    req = urllib.request.Request(
        f"{url}{path}/api/json{query}",
        headers={"Authorization": f"Basic {_basic(cred)}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}", "path": path}
    except urllib.error.URLError as e:
        return {"error": f"connection failed: {e.reason}"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def _ts(ms) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S") if ms else "?"


# ---------------------------------------------------------------------------
# Handlers (return plain dicts; wrapped into tool_result/tool_error at register)
# ---------------------------------------------------------------------------

def _job_info(folder="", job="", branch="", **_):
    cfg, err = _auth()
    if not cfg:
        return {"error": err}
    url, cred = cfg
    data = _get(url, _build_job_path(folder, job, branch), cred,
                "name,url,color,description,"
                "lastBuild[number,result,building,timestamp,url],"
                "lastCompletedBuild[number,result],lastFailedBuild[number,result],"
                "lastStableBuild[number,result],lastUnsuccessfulBuild[number,result],"
                "builds[number,result,timestamp]{0,5}")
    if "error" in data:
        return data

    def _fmt(b):
        if not b:
            return None
        return {"number": b.get("number"), "result": b.get("result"),
                "building": b.get("building"), "timestamp": _ts(b.get("timestamp", 0)),
                "url": b.get("url")}

    out = {"name": data.get("name"), "url": data.get("url"), "color": data.get("color"),
           "description": data.get("description", ""), "last_build": _fmt(data.get("lastBuild"))}
    for k in ("lastCompleted", "lastFailed", "lastStable", "lastUnsuccessful"):
        out[k] = _fmt(data.get(k + "Build"))
    out["recent_builds"] = [_fmt(b) for b in data.get("builds", []) if b]
    return out


def _build_info(folder="", job="", branch="", number=0, **_):
    if not number:
        return {"error": "build 'number' required"}
    cfg, err = _auth()
    if not cfg:
        return {"error": err}
    url, cred = cfg
    data = _get(url, f"{_build_job_path(folder, job, branch)}/{number}", cred,
                "number,result,building,timestamp,url,duration,estimatedDuration,"
                "fullDisplayName,description,builtOn,"
                "actions[parameters[name,value]],"
                "changeSets[items[author[fullName],msg,commitId,timestamp]]{0,10}")
    if "error" in data:
        return data
    out = {"number": data.get("number"), "result": data.get("result"),
           "building": data.get("building", False), "display_name": data.get("fullDisplayName"),
           "description": data.get("description", ""), "built_on": data.get("builtOn", ""),
           "duration_sec": data.get("duration", 0) / 1000,
           "estimated_sec": (data.get("estimatedDuration") or 0) / 1000 or None,
           "url": data.get("url"), "timestamp": _ts(data.get("timestamp", 0))}
    out["parameters"] = [{"name": p.get("name"), "value": p.get("value")}
                         for a in data.get("actions", []) for p in a.get("parameters", [])]
    out["changes"] = [
        {"author": (it.get("author") or {}).get("fullName", "?"), "message": it.get("msg", ""),
         "commit": (it.get("commitId") or "")[:12], "timestamp": _ts(it.get("timestamp", 0))}
        for cs in data.get("changeSets", []) for it in cs.get("items", [])
    ]
    return out


def _build_log(folder="", job="", branch="", number=0, tail=200, **_):
    if not number:
        return {"error": "build 'number' required"}
    cfg, err = _auth()
    if not cfg:
        return {"error": err}
    url, cred = cfg
    log_url = f"{url}{_build_job_path(folder, job, branch)}/{number}/logText/progressiveText"
    req = urllib.request.Request(
        log_url, headers={"Authorization": f"Basic {_basic(cred)}", "Accept": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            content = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"connection failed: {e.reason}"}
    lines = content.splitlines()
    total = len(lines)
    try:
        tail = int(tail)
    except (TypeError, ValueError):
        tail = 200
    if tail and tail < total:
        lines = lines[-tail:]
    return {"job": job, "build": number, "total_lines": total,
            "returned_lines": len(lines), "log": "\n".join(lines)}


def _build_trigger(folder="", job="", branch="", parameters=None, **_):
    cfg, err = _auth()
    if not cfg:
        return {"error": err}
    url, cred = cfg
    jpath = _build_job_path(folder, job, branch)
    if parameters:
        qs = "&".join(f"{urllib.request.quote(k)}={urllib.request.quote(str(v))}"
                      for k, v in parameters.items())
        full = f"{url}{jpath}/buildWithParameters?{qs}"
    else:
        full = f"{url}{jpath}/build?delay=0sec"
    req = urllib.request.Request(
        full, method="POST",
        headers={"Authorization": f"Basic {_basic(cred)}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"status": "queued", "queue_url": resp.headers.get("Location", ""), "job": job}
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"connection failed: {e.reason}"}


def _search(query="", **_):
    if not query:
        return {"error": "'query' required"}
    cfg, err = _auth()
    if not cfg:
        return {"error": err}
    url, cred = cfg
    ql = query.lower()
    data = _get(url, "", cred, "jobs[name,url,color,jobs[name,url,color,jobs[name,url,color]]]")
    if "error" in data:
        return data
    results = []

    def _recurse(jobs, path=""):
        for j in jobs:
            full = f"{path}/{j['name']}" if path else j["name"]
            if ql in j["name"].lower():
                results.append({"name": j["name"], "path": full.lstrip("/"),
                                "url": j.get("url", ""), "color": j.get("color")})
            if "jobs" in j:
                _recurse(j["jobs"], full)

    _recurse(data.get("jobs", []))
    return {"query": query, "matches": len(results), "results": results[:30]}


def _list_jobs(folder="", **_):
    cfg, err = _auth()
    if not cfg:
        return {"error": err}
    url, cred = cfg
    path = _build_job_path(folder) if folder else ""
    data = _get(url, path, cred, "jobs[name,color,url]")
    if "error" in data:
        return data
    return {"folder": folder or "(root)",
            "jobs": [{"name": j["name"], "color": j.get("color"), "url": j.get("url")}
                     for j in data.get("jobs", [])]}


# ---------------------------------------------------------------------------
# Schemas (flat form) + registration
# ---------------------------------------------------------------------------

_FOLDER = {"type": "string", "description": "Optional folder path (e.g. 'wirenboard')."}
_JOB = {"type": "string", "description": "Job name (e.g. 'wb-scenarios')."}
_BRANCH = {"type": "string", "description": "Optional branch/PR job for multibranch pipelines (e.g. 'PR-94')."}

JOB_INFO = {"name": "jenkins_job_info",
            "description": "Get a Jenkins job's details and recent builds (folder + multibranch aware).",
            "parameters": {"type": "object", "properties": {"folder": _FOLDER, "job": _JOB, "branch": _BRANCH},
                           "required": ["job"]}}
BUILD_INFO = {"name": "jenkins_build_info",
              "description": "Get one build: result, parameters, SCM changes, duration.",
              "parameters": {"type": "object", "properties": {"folder": _FOLDER, "job": _JOB, "branch": _BRANCH,
                             "number": {"type": "integer", "description": "Build number."}},
                             "required": ["job", "number"]}}
BUILD_LOG = {"name": "jenkins_build_log",
             "description": "Get a build's console log. tail=N returns the last N lines (default 200, 0=all).",
             "parameters": {"type": "object", "properties": {"folder": _FOLDER, "job": _JOB, "branch": _BRANCH,
                            "number": {"type": "integer", "description": "Build number."},
                            "tail": {"type": "integer", "description": "Max lines (default 200, 0=all)."}},
                            "required": ["job", "number"]}}
BUILD_TRIGGER = {"name": "jenkins_build_trigger",
                 "description": "Trigger a build. Pass 'parameters' (object) for a parameterized job. WRITE action.",
                 "parameters": {"type": "object", "properties": {"folder": _FOLDER, "job": _JOB, "branch": _BRANCH,
                                "parameters": {"type": "object", "description": "Build parameters (name->value).",
                                               "additionalProperties": {"type": "string"}}},
                                "required": ["job"]}}
SEARCH = {"name": "jenkins_search",
          "description": "Search Jenkins jobs by keyword across all folders.",
          "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Search keyword."}},
                         "required": ["query"]}}
LIST_JOBS = {"name": "jenkins_list_jobs",
             "description": "List jobs in a folder (root if folder empty).",
             "parameters": {"type": "object", "properties": {"folder": _FOLDER}, "required": []}}

_TOOLS = (
    (JOB_INFO, _job_info, "🔧"),
    (BUILD_INFO, _build_info, "📦"),
    (BUILD_LOG, _build_log, "📜"),
    (BUILD_TRIGGER, _build_trigger, "▶️"),
    (SEARCH, _search, "🔍"),
    (LIST_JOBS, _list_jobs, "📁"),
)


def _wrap(fn):
    """Adapt a dict-returning handler to the (args, **kw) -> tool_result/tool_error contract."""
    def handler(args, **_kw):
        try:
            result = fn(**(args or {}))
        except Exception as e:  # noqa: BLE001
            return tool_error(f"jenkins call failed: {e}")
        if isinstance(result, dict) and "error" in result:
            return tool_error(str(result["error"]))
        return tool_result(result)
    return handler


def register(ctx) -> None:
    for schema, fn, emoji in _TOOLS:
        ctx.register_tool(
            name=schema["name"],
            toolset="jenkins",
            schema=schema,
            handler=_wrap(fn),
            check_fn=_is_configured,
            emoji=emoji,
        )
    logger.info("jenkins plugin: registered %d tools", len(_TOOLS))
