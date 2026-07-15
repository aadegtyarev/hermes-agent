# Jenkins Plugin — Full Source

The Hermes agent internal plugin for Jenkins CI.

## Structure

```
~/.hermes/plugins/jenkins/
├── plugin.yaml
└── __init__.py
```

## plugin.yaml

```yaml
name: jenkins
version: 1.0.0
description: Jenkins CI integration — check jobs, builds, logs, trigger builds
author: Kern
provides_hooks: []
```

## __init__.py

```python
"""Jenkins CI plugin for Hermes Agent."""
import json, os, urllib.request, urllib.error
from typing import Optional

def _check_config():
    url = os.environ.get("JENKINS_URL", "").rstrip("/")
    user = os.environ.get("JENKINS_USER", "")
    token = os.environ.get("JENKINS_TOKEN", "")
    if not url:
        return None, False, "JENKINS_URL not set"
    if not token and not user:
        return None, False, "JENKINS_TOKEN or JENKINS_USER required"
    auth_user = token if token else user
    auth_str = f"{auth_user}:"
    return (url, user, token, auth_str), True, ""

def _build_job_path(folder: str, job: str, branch: str = "") -> str:
    parts = []
    if folder:
        for f in folder.strip("/").split("/"):
            parts.append(f"job/{urllib.request.quote(f, safe='')}")
    for jp in job.strip("/").split("/"):
        parts.append(f"job/{urllib.request.quote(jp, safe='')}")
    if branch:
        parts.append(f"job/{urllib.request.quote(branch, safe='')}")
    return "/" + "/".join(parts)

def _jenkins_get(url, path, auth, tree=None):
    query = f"?tree={urllib.request.quote(tree)}" if tree else ""
    auth_b64 = __import__("base64").b64encode(auth.encode()).decode()
    req = urllib.request.Request(
        f"{url}{path}/api/json{query}",
        headers={"Authorization": f"Basic {auth_b64}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}", "url": path}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}

def _handle_job_info(folder="", job="", branch="", **kw):
    cfg, ok, err = _check_config()
    if not ok: return {"error": err}
    url, user, token, auth = cfg
    jpath = _build_job_path(folder, job, branch)
    data = _jenkins_get(url, jpath, auth,
        "name,url,color,description,"
        "lastBuild[number,result,building,timestamp,url],"
        "lastCompletedBuild[number,result],lastFailedBuild[number,result],"
        "lastStableBuild[number,result],lastUnsuccessfulBuild[number,result],"
        "builds[number,result,timestamp]{0,5}")
    if "error" in data: return data
    result = {"name": data.get("name"), "url": data.get("url"),
              "color": data.get("color"), "description": data.get("description","")}
    def _fmt(b):
        if not b: return None
        ts = b.get("timestamp",0)
        from datetime import datetime
        return {"number": b.get("number"), "result": b.get("result"),
                "building": b.get("building"),
                "timestamp": datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d %H:%M:%S") if ts else "?",
                "url": b.get("url")}
    result["last_build"] = _fmt(data.get("lastBuild"))
    for k in ["lastCompleted","lastFailed","lastStable","lastUnsuccessful"]:
        result[k] = _fmt(data.get(k+"Build"))
    result["recent_builds"] = [_fmt(b) for b in data.get("builds",[]) if b]
    return result

def _handle_build_info(folder="", job="", branch="", number=0, **kw):
    if not number: return {"error": "build number required"}
    cfg, ok, err = _check_config()
    if not ok: return {"error": err}
    url, _, _, auth = cfg
    jpath = _build_job_path(folder, job, branch)
    data = _jenkins_get(url, f"{jpath}/{number}", auth,
        "number,result,building,timestamp,url,duration,estimatedDuration,"
        "fullDisplayName,description,builtOn,"
        "actions[parameters[name,value]],"
        "changeSets[items[author[fullName],msg,commitId,timestamp]]{0,10}")
    if "error" in data: return data
    from datetime import datetime
    r = {"number": data.get("number"), "result": data.get("result"),
         "building": data.get("building",False),
         "display_name": data.get("fullDisplayName"),
         "description": data.get("description",""),
         "built_on": data.get("builtOn",""),
         "duration_sec": data.get("duration",0)/1000,
         "estimated_sec": data.get("estimatedDuration",0)/1000 if data.get("estimatedDuration") else None,
         "url": data.get("url")}
    ts = data.get("timestamp",0)
    r["timestamp"] = datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d %H:%M:%S") if ts else "?"
    params = []
    for a in data.get("actions",[]):
        for p in a.get("parameters",[]):
            params.append({"name": p.get("name"), "value": p.get("value")})
    r["parameters"] = params
    changes = []
    for cs in data.get("changeSets",[]):
        for item in cs.get("items",[]):
            changes.append({
                "author": item.get("author",{}).get("fullName","?"),
                "message": item.get("msg",""),
                "commit": item.get("commitId","")[:12],
                "timestamp": datetime.fromtimestamp(item.get("timestamp",0)/1000).strftime("%Y-%m-%d %H:%M") if item.get("timestamp") else "?"
            })
    r["changes"] = changes
    return r

def _handle_build_log(folder="", job="", branch="", number=0, tail=200, **kw):
    if not number: return {"error": "build number required"}
    cfg, ok, err = _check_config()
    if not ok: return {"error": err}
    url, _, _, auth = cfg
    jpath = _build_job_path(folder, job, branch)
    auth_b64 = __import__("base64").b64encode(auth.encode()).decode()
    log_url = f"{url}{jpath}/{number}/logText/progressiveText"
    req = urllib.request.Request(log_url, headers={"Authorization": f"Basic {auth_b64}", "Accept": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            content = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}", "url": log_url}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}
    lines = content.splitlines()
    total = len(lines)
    if tail and tail < total: lines = lines[-tail:]
    return {"job": job, "build": number, "total_lines": total,
            "returned_lines": len(lines), "log": "\n".join(lines)}

def _handle_build_trigger(folder="", job="", branch="", parameters=None, **kw):
    cfg, ok, err = _check_config()
    if not ok: return {"error": err}
    url, _, _, auth = cfg
    jpath = _build_job_path(folder, job, branch)
    auth_b64 = __import__("base64").b64encode(auth.encode()).decode()
    if parameters:
        qs = "&".join(f"{urllib.request.quote(k)}={urllib.request.quote(str(v))}" for k,v in parameters.items())
        full_url = f"{url}{jpath}/buildWithParameters?{qs}"
    else:
        full_url = f"{url}{jpath}/build?delay=0sec"
    req = urllib.request.Request(full_url, method="POST",
        headers={"Authorization": f"Basic {auth_b64}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            loc = resp.headers.get("Location","")
            return {"status": "queued", "queue_url": loc, "job": job}
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}

def _handle_search(query="", **kw):
    if not query: return {"error": "query required"}
    cfg, ok, err = _check_config()
    if not ok: return {"error": err}
    url, _, _, auth = cfg
    ql = query.lower()
    data = _jenkins_get(url, "", auth, "jobs[name,url,color,jobs[name,url,color,jobs[name,url,color]]]")
    if "error" in data: return data
    results = []
    def _recurse(jobs, path=""):
        for j in jobs:
            full = f"{path}/{j['name']}" if path else j["name"]
            if ql in j["name"].lower():
                results.append({"name": j["name"], "path": full.lstrip("/"),
                                "url": j.get("url",""), "color": j.get("color")})
            if "jobs" in j:
                _recurse(j["jobs"], full)
    _recurse(data.get("jobs",[]))
    return {"query": query, "matches": len(results), "results": results[:30]}

def _handle_list_jobs(folder="", **kw):
    cfg, ok, err = _check_config()
    if not ok: return {"error": err}
    url, _, _, auth = cfg
    path = _build_job_path(folder, "") if folder else ""
    data = _jenkins_get(url, path, auth, "jobs[name,color,url]")
    if "error" in data: return data
    return {"folder": folder or "(root)", "jobs": [
        {"name": j["name"], "color": j.get("color"), "url": j.get("url")}
        for j in data.get("jobs",[])
    ]}

TOOLS = [
    ("jenkins_job_info",  {"type":"function","function":{"name":"jenkins_job_info",
        "description":"Get job details and last builds. Supports folder, multibranch branch.",
        "parameters":{"type":"object","properties":{
            "folder":{"type":"string","description":"Optional folder path (e.g. 'wirenboard')."},
            "job":{"type":"string","description":"Job name (e.g. 'build-zigbee2mqtt')."},
            "branch":{"type":"string","description":"Optional branch for multibranch pipelines."}},
            "required":["job"]}}}, _handle_job_info, "\U0001f527"),
    ("jenkins_build_info", {"type":"function","function":{"name":"jenkins_build_info",
        "description":"Get specific build: params, changes, duration, result.",
        "parameters":{"type":"object","properties":{
            "folder":{"type":"string","description":"Optional folder path."},
            "job":{"type":"string","description":"Job name."},
            "branch":{"type":"string","description":"Optional branch for multibranch."},
            "number":{"type":"integer","description":"Build number."}},
            "required":["job","number"]}}}, _handle_build_info, "\U0001f527"),
    ("jenkins_build_log", {"type":"function","function":{"name":"jenkins_build_log",
        "description":"Get build console log. tail=N for last N lines (default 200, 0=all).",
        "parameters":{"type":"object","properties":{
            "folder":{"type":"string","description":"Optional folder path."},
            "job":{"type":"string","description":"Job name."},
            "branch":{"type":"string","description":"Optional branch for multibranch."},
            "number":{"type":"integer","description":"Build number."},
            "tail":{"type":"integer","description":"Max lines (default 200, 0=all)."}},
            "required":["job","number"]}}}, _handle_build_log, "\U0001f527"),
    ("jenkins_build_trigger", {"type":"function","function":{"name":"jenkins_build_trigger",
        "description":"Trigger a build. Optional parameters dict for parameterized builds.",
        "parameters":{"type":"object","properties":{
            "folder":{"type":"string","description":"Optional folder path."},
            "job":{"type":"string","description":"Job name."},
            "branch":{"type":"string","description":"Optional branch for multibranch."},
            "parameters":{"type":"object","description":"Build parameters (key-value).",
                "additionalProperties":{"type":"string"}}},
            "required":["job"]}}}, _handle_build_trigger, "\U0001f527"),
    ("jenkins_search", {"type":"function","function":{"name":"jenkins_search",
        "description":"Search Jenkins jobs by keyword across all folders.",
        "parameters":{"type":"object","properties":{
            "query":{"type":"string","description":"Search keyword."}},
            "required":["query"]}}}, _handle_search, "\U0001f50d"),
    ("jenkins_list_jobs", {"type":"function","function":{"name":"jenkins_list_jobs",
        "description":"List jobs in a folder (root if folder empty).",
        "parameters":{"type":"object","properties":{
            "folder":{"type":"string","description":"Folder path (e.g. 'wirenboard'). Leave empty for root."}},
            "required":[]}}}, _handle_list_jobs, "\U0001f4c1"),
]

def register(ctx):
    for name, schema, handler, emoji in TOOLS:
        ctx.register_tool(name, schema, handler, emoji)
```
