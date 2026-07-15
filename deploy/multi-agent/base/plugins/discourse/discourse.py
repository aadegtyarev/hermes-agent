"""Discourse API plugin for Hermes — read-only search, thread reading, and
attachment/image extraction for a Discourse forum (default: the WB support
portal at support.wirenboard.com).

Read-only by design: there is no reply/post/like/edit/delete tool anywhere
in this plugin, on purpose. The real write-blocker is the API key itself —
see README.md for how to mint a Discourse "Read Only" scoped key tied to a
dedicated low-trust service account — but the plugin never gives the model
a POST tool to begin with, so even a fully-scoped key can't be misused
through this integration.

Configuration via env vars:
  DISCOURSE_URL           — base URL, e.g. https://support.wirenboard.com
                            (defaults to the WB support portal if unset)
  DISCOURSE_API_KEY       — optional. Without it, every call is anonymous
                            and only sees what a logged-out visitor sees.
  DISCOURSE_API_USERNAME  — required alongside DISCOURSE_API_KEY (Discourse
                            auth is the pair Api-Key + Api-Username).
"""

from __future__ import annotations

import html as _html_mod
import json
import os
import re

import requests


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DISCOURSE_URL = (os.environ.get("DISCOURSE_URL", "").rstrip("/")
                  or "https://support.wirenboard.com")
DISCOURSE_API_KEY = os.environ.get("DISCOURSE_API_KEY", "")
DISCOURSE_API_USERNAME = os.environ.get("DISCOURSE_API_USERNAME", "")

_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _session() -> requests.Session:
    s = requests.Session()
    s.headers["Accept"] = "application/json"
    s.headers["User-Agent"] = "hermes-discourse-plugin/1.0"
    if DISCOURSE_API_KEY and DISCOURSE_API_USERNAME:
        s.headers["Api-Key"] = DISCOURSE_API_KEY
        s.headers["Api-Username"] = DISCOURSE_API_USERNAME
    return s


def _get(path: str, params=None, timeout: int = 30) -> dict | list:
    """GET request to the Discourse JSON API. Returns parsed JSON or {"error": ...}."""
    url = f"{DISCOURSE_URL}{path}"
    try:
        r = _session().get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        detail = ""
        try:
            detail = e.response.text[:300]
        except Exception:
            pass
        return {"error": f"HTTP {e.response.status_code}: {detail}"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _err(msg: str, **extra) -> str:
    return _json({"success": False, "error": msg, **extra})


# ---------------------------------------------------------------------------
# cooked-HTML helpers (post body -> plain text, attachment/image extraction)
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"[^>]*>')
_ATTACH_RE = re.compile(r'<a class="attachment" href="([^"]+)">([^<]*)</a>')
_SKIP_IMG_SUBSTRINGS = ("/images/emoji/", "/user_avatar/", "/letter_avatar")


def _abs_url(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return DISCOURSE_URL + url
    return url


def _html_to_text(cooked: str, max_len: int = 4000) -> str:
    if not cooked:
        return ""
    text = _TAG_RE.sub(" ", cooked)
    text = _html_mod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_len:
        text = text[:max_len] + "…"
    return text


def _extract_attachments(cooked: str) -> list:
    if not cooked:
        return []
    out = []
    for m in _IMG_RE.finditer(cooked):
        src = m.group(1)
        if any(s in src for s in _SKIP_IMG_SUBSTRINGS):
            continue
        out.append({"kind": "image", "url": _abs_url(src)})
    for m in _ATTACH_RE.finditer(cooked):
        href, name = m.group(1), m.group(2).strip()
        out.append({"kind": "file", "url": _abs_url(href), "filename": name or None})
    return out


def _post_view(p: dict, text_len: int) -> dict:
    return {
        "post_number": p.get("post_number"),
        "id": p.get("id"),
        "username": p.get("username"),
        "staff": bool(p.get("staff")),
        "created_at": p.get("created_at"),
        "updated_at": p.get("updated_at"),
        "like_count": p.get("like_count", 0),
        "accepted_answer": bool(p.get("accepted_answer")),
        "text": _html_to_text(p.get("cooked", ""), max_len=text_len),
        "attachments": _extract_attachments(p.get("cooked", "")),
    }


def _home() -> str:
    try:
        from hermes_constants import get_hermes_home
        return str(get_hermes_home())
    except Exception:
        return os.getcwd()


# ---------------------------------------------------------------------------
# Topic/posts fetch + pagination (post_stream only ships the first ~20 posts;
# the rest of the ids live in post_stream.stream and need a follow-up fetch)
# ---------------------------------------------------------------------------

def _fetch_topic_posts(topic_id: str, max_posts: int) -> dict:
    data = _get(f"/t/{topic_id}.json")
    if isinstance(data, dict) and "error" in data:
        return data

    stream_obj = data.get("post_stream", {}) if isinstance(data, dict) else {}
    posts = list(stream_obj.get("posts", []))
    all_ids = stream_obj.get("stream", []) or []
    have_ids = {p.get("id") for p in posts}
    missing = [i for i in all_ids if i not in have_ids]

    while missing and len(posts) < max_posts:
        batch, missing = missing[:50], missing[50:]
        params = [("post_ids[]", i) for i in batch]
        extra = _get(f"/t/{topic_id}/posts.json", params=params)
        if isinstance(extra, dict) and "error" in extra:
            break
        extra_posts = (extra or {}).get("post_stream", {}).get("posts", [])
        if not extra_posts:
            break
        posts.extend(extra_posts)

    posts.sort(key=lambda p: p.get("post_number") or 0)
    return {"topic": data, "posts": posts[:max_posts], "total_posts": len(all_ids) or len(posts)}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

DISCOURSE_SEARCH_SCHEMA = {
    "name": "discourse_search",
    "description": (
        "Flexible read-only search of the Discourse forum (default: "
        "support.wirenboard.com). Combines a free-text query with structured "
        "filters (category, tags, author, status, date range) using Discourse's "
        "advanced search syntax. Returns matching topics and post excerpts. "
        "Read-only: there is no reply/post tool in this plugin."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Free-text search terms."},
            "category": {"type": "string", "description": "Category slug to restrict to, e.g. 'support' or 'hardware'."},
            "tags": {"type": "string", "description": "Comma-separated tags, e.g. 'modbus,wb-mqtt-serial'."},
            "username": {"type": "string", "description": "Only posts authored by this forum username."},
            "status": {
                "type": "string",
                "description": "One of: open, closed, archived, noreplies, single_user, solved, unsolved.",
            },
            "order": {
                "type": "string",
                "description": "One of: latest, latest_topic, oldest, views, likes. Default: relevance.",
            },
            "in_title": {"type": "boolean", "description": "Restrict search to topic titles only."},
            "before": {"type": "string", "description": "YYYY-MM-DD — only results before this date."},
            "after": {"type": "string", "description": "YYYY-MM-DD — only results after this date."},
            "max_results": {"type": "integer", "description": "Max results to return (default 20, max 50)."},
        },
        "required": [],
    },
}

DISCOURSE_GET_TOPIC_SCHEMA = {
    "name": "discourse_get_topic",
    "description": (
        "Read a full Discourse thread by topic id: title, tags, category, and "
        "posts in order (author, staff flag, timestamps, plain-text body, and "
        "any attachments/images found in that post). Paginates automatically "
        "for long threads, up to max_posts. Read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic_id": {"type": ["string", "integer"], "description": "Numeric topic id (from a URL like /t/slug/1234)."},
            "max_posts": {"type": "integer", "description": "Max posts to return (default 30, max 200)."},
            "text_len": {"type": "integer", "description": "Max characters of post body to return per post (default 2000)."},
        },
        "required": ["topic_id"],
    },
}

DISCOURSE_LIST_LATEST_SCHEMA = {
    "name": "discourse_list_latest",
    "description": (
        "List the most recently active topics on the forum, optionally scoped "
        "to a category. Good for a periodic sweep of new/active activity. Read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "Category slug to restrict to (optional)."},
            "max_results": {"type": "integer", "description": "Max topics to return (default 30, max 100)."},
        },
        "required": [],
    },
}

DISCOURSE_LIST_CATEGORIES_SCHEMA = {
    "name": "discourse_list_categories",
    "description": (
        "List forum categories (id, slug, name, topic_count, and whether the "
        "category is read-restricted). Use this to discover the 'category' "
        "value for discourse_search / discourse_list_latest. Read-only."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

DISCOURSE_GET_ATTACHMENTS_SCHEMA = {
    "name": "discourse_get_attachments",
    "description": (
        "Extract (and optionally download) images/attachments from a topic. "
        "Without download=true, returns the list of attachment/image URLs found "
        "in the thread. With download=true, downloads each file (capped) into "
        "the agent's data directory and returns local file paths instead. "
        "Read-only — this never modifies anything on the forum."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic_id": {"type": ["string", "integer"], "description": "Numeric topic id."},
            "post_number": {"type": "integer", "description": "Restrict to a single post number in the topic (optional)."},
            "download": {"type": "boolean", "description": "If true, download files locally instead of just listing URLs. Default false."},
            "max_files": {"type": "integer", "description": "Max files to list/download (default 10, max 30)."},
        },
        "required": ["topic_id"],
    },
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_discourse_search(args, **_kw) -> str:
    query = str(args.get("query") or "").strip()
    category = str(args.get("category") or "").strip()
    tags = str(args.get("tags") or "").strip()
    username = str(args.get("username") or "").strip()
    status = str(args.get("status") or "").strip()
    order = str(args.get("order") or "").strip()
    in_title = bool(args.get("in_title") or False)
    before = str(args.get("before") or "").strip()
    after = str(args.get("after") or "").strip()
    max_results = min(int(args.get("max_results") or 20), 50)

    if not any([query, category, tags, username, status, before, after]):
        return _err("At least one of query/category/tags/username/status/before/after is required.")

    parts = []
    if query:
        parts.append(query)
    if category:
        parts.append(f"category:{category}")
    if tags:
        parts.append(f"tags:{tags}")
    if username:
        parts.append(f"@{username}")
    if status:
        parts.append(f"status:{status}")
    if order:
        parts.append(f"order:{order}")
    if in_title:
        parts.append("in:title")
    if before:
        parts.append(f"before:{before}")
    if after:
        parts.append(f"after:{after}")
    q = " ".join(parts)

    result = _get("/search.json", params={"q": q})
    if isinstance(result, dict) and "error" in result:
        return _err(result["error"])

    topics = (result or {}).get("topics", [])[:max_results]
    posts = (result or {}).get("posts", [])[:max_results]
    return _json({
        "success": True,
        "query": q,
        "topic_count": len(topics),
        "topics": [
            {
                "id": t.get("id"), "title": t.get("title"), "slug": t.get("slug"),
                "created_at": t.get("created_at"), "bumped_at": t.get("bumped_at"),
                "reply_count": t.get("reply_count"), "tags": t.get("tags"),
                "category_id": t.get("category_id"),
            } for t in topics
        ],
        "post_count": len(posts),
        "posts": [
            {
                "topic_id": p.get("topic_id"), "post_number": p.get("post_number"),
                "username": p.get("username"), "created_at": p.get("created_at"),
                "blurb": p.get("blurb"),
            } for p in posts
        ],
    })


def handle_discourse_get_topic(args, **_kw) -> str:
    topic_id = str(args.get("topic_id") or "").strip()
    if not topic_id:
        return _err("Missing required arg: 'topic_id'")
    max_posts = min(int(args.get("max_posts") or 30), 200)
    text_len = min(int(args.get("text_len") or 2000), 8000)

    fetched = _fetch_topic_posts(topic_id, max_posts)
    if "error" in fetched:
        return _err(fetched["error"])

    topic = fetched["topic"]
    posts = [_post_view(p, text_len) for p in fetched["posts"]]
    return _json({
        "success": True,
        "topic_id": topic.get("id"),
        "title": topic.get("title"),
        "slug": topic.get("slug"),
        "category_id": topic.get("category_id"),
        "tags": topic.get("tags"),
        "created_at": topic.get("created_at"),
        "views": topic.get("views"),
        "posts_count_total": fetched["total_posts"],
        "posts_returned": len(posts),
        "posts": posts,
    })


def handle_discourse_list_latest(args, **_kw) -> str:
    category = str(args.get("category") or "").strip()
    max_results = min(int(args.get("max_results") or 30), 100)

    path = f"/c/{category}/l/latest.json" if category else "/latest.json"
    result = _get(path)
    if isinstance(result, dict) and "error" in result:
        return _err(result["error"])

    topics = (result or {}).get("topic_list", {}).get("topics", [])[:max_results]
    return _json({
        "success": True,
        "count": len(topics),
        "topics": [
            {
                "id": t.get("id"), "title": t.get("title"), "slug": t.get("slug"),
                "category_id": t.get("category_id"), "tags": t.get("tags"),
                "created_at": t.get("created_at"), "bumped_at": t.get("bumped_at"),
                "last_poster_username": t.get("last_poster_username"),
                "reply_count": t.get("reply_count"), "views": t.get("views"),
                "pinned_globally": t.get("pinned_globally", False),
                "has_accepted_answer": t.get("has_accepted_answer", False),
            } for t in topics
        ],
    })


def handle_discourse_list_categories(args, **_kw) -> str:
    result = _get("/categories.json")
    if isinstance(result, dict) and "error" in result:
        return _err(result["error"])

    cats = (result or {}).get("category_list", {}).get("categories", [])
    return _json({
        "success": True,
        "count": len(cats),
        "categories": [
            {
                "id": c.get("id"), "slug": c.get("slug"), "name": c.get("name"),
                "topic_count": c.get("topic_count"),
                "read_restricted": c.get("read_restricted", False),
                "description": c.get("description_text"),
            } for c in cats
        ],
    })


def handle_discourse_get_attachments(args, **_kw) -> str:
    topic_id = str(args.get("topic_id") or "").strip()
    if not topic_id:
        return _err("Missing required arg: 'topic_id'")
    post_number = args.get("post_number")
    download = bool(args.get("download") or False)
    max_files = min(int(args.get("max_files") or 10), 30)

    fetched = _fetch_topic_posts(topic_id, max_posts=200)
    if "error" in fetched:
        return _err(fetched["error"])

    posts = fetched["posts"]
    if post_number is not None:
        posts = [p for p in posts if p.get("post_number") == int(post_number)]
        if not posts:
            return _err(f"post_number {post_number} not found in topic {topic_id}")

    items = []
    for p in posts:
        for a in _extract_attachments(p.get("cooked", "")):
            a = dict(a)
            a["post_number"] = p.get("post_number")
            a["username"] = p.get("username")
            items.append(a)
            if len(items) >= max_files:
                break
        if len(items) >= max_files:
            break

    if not download:
        return _json({"success": True, "topic_id": topic_id, "count": len(items), "attachments": items})

    home = _home()
    dest_dir = os.path.join(home, "data", "discourse_downloads", f"topic_{topic_id}")
    os.makedirs(dest_dir, exist_ok=True)

    downloaded = []
    sess = _session()
    for i, a in enumerate(items):
        url = a["url"]
        fname = a.get("filename") or os.path.basename(url.split("?")[0]) or f"file_{i}"
        dest = os.path.normpath(os.path.join(dest_dir, fname))
        try:
            r = sess.get(url, timeout=30, stream=True)
            r.raise_for_status()
            total, chunks = 0, []
            for chunk in r.iter_content(8192):
                total += len(chunk)
                if total > _MAX_DOWNLOAD_BYTES:
                    break
                chunks.append(chunk)
            with open(dest, "wb") as f:
                f.write(b"".join(chunks))
            downloaded.append({**a, "saved_to": dest, "bytes": total})
        except Exception as e:  # noqa: BLE001
            downloaded.append({**a, "error": str(e)})

    return _json({"success": True, "topic_id": topic_id, "count": len(downloaded), "attachments": downloaded})


# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

def check_discourse_deps() -> bool:
    """Tool availability gate. Auth is optional — anonymous access to public
    content works out of the box; DISCOURSE_API_KEY only extends reach."""
    try:
        import requests  # noqa: F401
        return bool(DISCOURSE_URL)
    except ImportError:
        return False
