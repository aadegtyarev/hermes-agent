"""GitHub plugin — tool handlers using gh CLI and REST API."""

import re

import json


# Regex for terminal commands that try to bypass github tools
_GH_BYPASS_RE = re.compile(
    r"(?:^|[\s;&|])(?:gh\s|git\s+push|git\s+clone\s+.*github|"
    r"github\.com/[^/\s]+/[^/\s]+/(?:issues|pull)|"
    r"api\.github\.com)",
    re.IGNORECASE,
)
import os
import subprocess
import urllib.request
import urllib.error


def _token():
    return os.environ.get("GITHUB_TOKEN", "")


def _allowed(repo: str) -> bool:
    """Check if repo is in GITHUB_ALLOWED_REPOS allowlist. Empty list = allow all."""
    allowlist = os.environ.get("GITHUB_ALLOWED_REPOS", "").strip()
    if not allowlist:
        return True
    allowed = {r.strip().lower() for r in allowlist.split(",") if r.strip()}
    return repo.lower() in allowed


def _allowed_repos_str() -> str:
    """Return comma-separated allowed repos, or empty string if all allowed."""
    allowlist = os.environ.get("GITHUB_ALLOWED_REPOS", "").strip()
    return allowlist


def _gate(repo: str) -> str | None:
    """Return error JSON if repo is not allowed, or None if OK.
    The message is designed to be clear to the LLM: this is a policy restriction,
    NOT a transient error — do NOT retry or try workarounds."""
    if not _allowed(repo):
        allowed = sorted({
            r.strip().lower() for r in
            os.environ.get("GITHUB_ALLOWED_REPOS", "").split(",")
            if r.strip()
        })
        return json.dumps({
            "policy_restriction": True,
            "message": (
                f"Repository '{repo}' is not in the GITHUB_ALLOWED_REPOS allowlist. "
                f"This is a security policy — do NOT attempt to access this repo "
                f"through terminal, gh CLI, curl, or any other means. "
                f"Allowed repos: {', '.join(allowed)}."
            ),
        })
    return None


def _api(url, method="GET", data=None):
    """Call GitHub REST API. Returns (status, body_dict)."""
    token = _token()
    if not token:
        return None, {"error": "GITHUB_TOKEN not set"}
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode() if data else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "hermes-github-plugin",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
            return resp.status, body
    except urllib.error.HTTPError as e:
        err = json.loads(e.read().decode()) if e.fp else {}
        return e.code, {"error": err.get("message", str(e))}
    except Exception as e:
        return 0, {"error": str(e)}


def _gh(args, stdin=None):
    """Call gh CLI. Returns (rc, stdout_text)."""
    try:
        r = subprocess.run(
            ["gh"] + args,
            capture_output=True, text=True,
            input=stdin,
            timeout=30,
        )
        return r.returncode, r.stdout.strip()
    except FileNotFoundError:
        return -1, "gh CLI not found — install from https://cli.github.com"
    except subprocess.TimeoutExpired:
        return -1, "gh CLI timed out"


def _gh_json(args):
    """Call gh with --json and parse result."""
    rc, out = _gh(args)
    if rc != 0:
        return None, {"error": out or f"gh exited with code {rc}"}
    try:
        return json.loads(out), None
    except json.JSONDecodeError:
        return None, {"error": f"gh returned non-JSON: {out[:300]}"}


# ── Issue tools ──────────────────────────────────────────────

def issue_list(args, **kwargs):
    """List issues with filtering."""
    repo = args.get("repo", "")
    if err := _gate(repo):
        return err
    state = args.get("state", "open")
    labels = args.get("labels", "")
    assignee = args.get("assignee", "")
    limit = args.get("limit", 20)
    search = args.get("search", "")

    gh_args = ["issue", "list", "--repo", repo,
               "--state", state, "--limit", str(limit),
               "--json", "number,title,state,labels,assignees,url,createdAt"]

    if labels:
        for lb in labels.split(","):
            gh_args.extend(["--label", lb.strip()])
    if assignee:
        gh_args.extend(["--assignee", assignee])
    if search:
        gh_args.extend(["--search", search])

    issues, err = _gh_json(gh_args)
    if err:
        return json.dumps(err)

    return json.dumps({
        "repo": repo,
        "count": len(issues),
        "issues": [{
            "number": i.get("number"),
            "title": i.get("title"),
            "state": i.get("state"),
            "labels": [lb["name"] for lb in (i.get("labels") or [])],
            "assignees": [a["login"] for a in (i.get("assignees") or [])],
            "url": i.get("url"),
            "created": i.get("createdAt"),
        } for i in issues],
    })


def issue_view(args, **kwargs):
    """View single issue with optional comments."""
    repo = args["repo"]
    if err := _gate(repo):
        return err
    number = args["number"]
    include_comments = args.get("include_comments", True)

    issue, err = _gh_json(["issue", "view", str(number),
                           "--repo", repo,
                           "--json", "number,title,state,body,labels,assignees,url,createdAt,updatedAt,comments"])
    if err:
        return json.dumps(err)

    result = {
        "repo": repo,
        "number": issue.get("number"),
        "title": issue.get("title"),
        "state": issue.get("state"),
        "body": issue.get("body"),
        "labels": [lb["name"] for lb in (issue.get("labels") or [])],
        "assignees": [a["login"] for a in (issue.get("assignees") or [])],
        "url": issue.get("url"),
        "created": issue.get("createdAt"),
        "updated": issue.get("updatedAt"),
    }

    if include_comments and issue.get("comments", 0) > 0:
        cmts, _ = _gh_json(["issue", "view", str(number),
                            "--repo", repo,
                            "--comments",
                            "--json", "author,body,createdAt"])
        if cmts and isinstance(cmts, dict):
            result["comments"] = [{
                "author": c.get("author", {}).get("login", "unknown"),
                "body": c.get("body", ""),
                "created": c.get("createdAt"),
            } for c in cmts.get("comments", [])]

    return json.dumps(result, ensure_ascii=False)


def issue_create(args, **kwargs):
    """Create a new issue."""
    repo = args["repo"]
    if err := _gate(repo):
        return err
    title = args["title"]
    body = args.get("body", "")
    labels = args.get("labels", "")
    assignee = args.get("assignee", "")

    gh_args = ["issue", "create", "--repo", repo, "--title", title]
    if body:
        gh_args.extend(["--body", body])
    for lb in (labels.split(",") if labels else []):
        lb = lb.strip()
        if lb:
            gh_args.extend(["--label", lb])
    if assignee:
        gh_args.extend(["--assignee", assignee])

    issue, err = _gh_json(gh_args)
    if err:
        return json.dumps(err)

    return json.dumps({
        "created": True,
        "url": issue.get("url"),
        "number": issue.get("number"),
        "title": issue.get("title"),
    })


# ── PR tools ──────────────────────────────────────────────────

def pr_list(args, **kwargs):
    """List pull requests with filtering."""
    repo = args.get("repo", "")
    if err := _gate(repo):
        return err
    state = args.get("state", "open")
    author = args.get("author", "")
    labels = args.get("labels", "")
    limit = args.get("limit", 10)

    gh_args = ["pr", "list", "--repo", repo,
               "--state", state, "--limit", str(limit),
               "--json", "number,title,state,author,headRefName,baseRefName,isDraft,statusChecks,url,createdAt"]

    if author:
        gh_args.extend(["--author", author])
    if labels:
        for lb in labels.split(","):
            gh_args.extend(["--label", lb.strip()])

    prs, err = _gh_json(gh_args)
    if err:
        return json.dumps(err)

    return json.dumps({
        "repo": repo,
        "count": len(prs),
        "prs": [{
            "number": p.get("number"),
            "title": p.get("title"),
            "state": p.get("state"),
            "author": p.get("author", {}).get("login", "unknown"),
            "branch": f"{p.get('headRefName', '?')} → {p.get('baseRefName', '?')}",
            "draft": p.get("isDraft", False),
            "ci_checks": p.get("statusChecks", {}).get("state", "UNKNOWN") if isinstance(p.get("statusChecks"), dict) else "UNKNOWN",
            "url": p.get("url"),
            "created": p.get("createdAt"),
        } for p in prs],
    })


def pr_view(args, **kwargs):
    """View PR details: description, files, reviews, CI, mergeable."""
    repo = args["repo"]
    if err := _gate(repo):
        return err
    number = args["number"]

    pr, err = _gh_json(["pr", "view", str(number), "--repo", repo,
                        "--json", "number,title,state,body,author,headRefName,baseRefName,isDraft,mergeable,reviews,statusCheckRollup,files,additions,deletions,url,createdAt,updatedAt"])
    if err:
        return json.dumps(err)

    files_list = []
    for f in (pr.get("files") or []):
        files_list.append(
            f"{f.get('path', '?')} (+{f.get('additions', 0)} -{f.get('deletions', 0)})"
        )

    reviews_list = []
    for r in (pr.get("reviews") or []):
        reviews_list.append({
            "reviewer": r.get("author", {}).get("login", "unknown"),
            "state": r.get("state"),
            "body": (r.get("body") or "")[:300],
        })

    ci_checks = []
    for c in (pr.get("statusCheckRollup") or []):
        ci_checks.append(f"{c.get('name')}: {c.get('conclusion', 'PENDING')}")

    return json.dumps({
        "repo": repo,
        "number": pr.get("number"),
        "title": pr.get("title"),
        "state": pr.get("state"),
        "author": pr.get("author", {}).get("login", "unknown"),
        "branch": f"{pr.get('headRefName')} → {pr.get('baseRefName')}",
        "draft": pr.get("isDraft", False),
        "mergeable": pr.get("mergeable"),
        "body": pr.get("body"),
        "changes": f"+{pr.get('additions', 0)} -{pr.get('deletions', 0)} in {len(files_list)} files",
        "files": files_list[:20],
        "reviews": reviews_list[:10],
        "ci_checks": ci_checks[:15],
        "url": pr.get("url"),
        "created": pr.get("createdAt"),
        "updated": pr.get("updatedAt"),
    }, ensure_ascii=False)


def pr_merge(args, **kwargs):
    """Merge a PR."""
    repo = args["repo"]
    if err := _gate(repo):
        return err
    number = args["number"]
    method = args.get("method", "merge")
    delete_branch = args.get("delete_branch", False)

    gh_args = ["pr", "merge", str(number), "--repo", repo,
               f"--{method}"]
    if delete_branch:
        gh_args.append("--delete-branch")

    rc, out = _gh(gh_args)
    if rc != 0:
        return json.dumps({"merged": False, "error": out or f"gh exited {rc}"})

    return json.dumps({
        "merged": True,
        "repo": repo,
        "number": number,
        "method": method,
        "output": out,
    })


# ── Search ────────────────────────────────────────────────────

def repo_search(args, **kwargs):
    """Search code, issues, and PRs in a repo."""
    repo = args["repo"]
    if err := _gate(repo):
        return err
    query = args["query"]
    stype = args.get("type", "all")
    limit = args.get("limit", 10)

    result = {"repo": repo, "query": query}

    if stype in ("code", "all"):
        code_out, _ = _gh_json([
            "search", "code", query,
            "--repo", repo,
            "--limit", str(limit),
            "--json", "path,repository",
        ])
        if code_out is not None:
            result["code_matches"] = [
                {"path": c.get("path"), "repo": (c.get("repository") or {}).get("full_name", repo)}
                for c in code_out
            ]

    if stype in ("issues", "all"):
        issues_out, _ = _gh_json([
            "search", "issues", query,
            "--repo", repo,
            "--limit", str(limit),
            "--json", "number,title,state,url",
        ])
        if issues_out is not None:
            result["issue_matches"] = [
                {"number": i.get("number"), "title": i.get("title"),
                 "state": i.get("state"), "url": i.get("url")}
                for i in issues_out
            ]

    if stype in ("prs", "all"):
        prs_out, _ = _gh_json([
            "search", "prs", query,
            "--repo", repo,
            "--limit", str(limit),
            "--json", "number,title,state,url",
        ])
        if prs_out is not None:
            result["pr_matches"] = [
                {"number": p.get("number"), "title": p.get("title"),
                 "state": p.get("state"), "url": p.get("url")}
                for p in prs_out
            ]

    return json.dumps(result, ensure_ascii=False)
