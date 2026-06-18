"""Discourse HTTP client (read-only).

All configuration is resolved PARENT-SIDE: tool handlers run in the gateway
process, so the API key (when set) never reaches the model or the
``execute_code`` sandbox. Reads from env first, then ``discourse.*`` in
config.yaml. The API key is optional — public forums are searchable and
readable without it.
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional

import httpx

_HTTP: Optional[httpx.Client] = None


def _http() -> httpx.Client:
    global _HTTP
    if _HTTP is None:
        _HTTP = httpx.Client(timeout=30.0, follow_redirects=True)
    return _HTTP


def _val(env_name: str, section: str, key: str, default: str = "") -> str:
    """Resolve a setting from env, then config.yaml, then default. Parent-side."""
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
    return _val("DISCOURSE_URL", "discourse", "url").rstrip("/")


def is_configured() -> bool:
    """True when a base URL is set (the API key is optional)."""
    return bool(_base_url())


def _headers() -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    api_key = _val("DISCOURSE_API_KEY", "discourse", "api_key")
    if api_key:
        headers["Api-Key"] = api_key
        headers["Api-Username"] = _val(
            "DISCOURSE_API_USERNAME", "discourse", "api_username", default="system"
        )
    return headers


def _strip_html(text: str, limit: int = 2000) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", text or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + " […]"
    return text


def search(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """Search topics/posts. Returns a compact list of matching topics."""
    base = _base_url()
    resp = _http().get(
        f"{base}/search.json", params={"q": query}, headers=_headers()
    )
    resp.raise_for_status()
    data = resp.json() or {}
    topics = {t.get("id"): t for t in (data.get("topics") or [])}
    out: List[Dict[str, Any]] = []
    for post in (data.get("posts") or [])[:max_results]:
        tid = post.get("topic_id")
        topic = topics.get(tid, {})
        out.append(
            {
                "topic_id": tid,
                "title": topic.get("title") or topic.get("fancy_title"),
                "url": f"{base}/t/{tid}" if tid else None,
                "blurb": _strip_html(post.get("blurb", ""), limit=300),
                "created_at": post.get("created_at"),
            }
        )
    if not out:
        for topic in (data.get("topics") or [])[:max_results]:
            tid = topic.get("id")
            out.append(
                {
                    "topic_id": tid,
                    "title": topic.get("title") or topic.get("fancy_title"),
                    "url": f"{base}/t/{tid}" if tid else None,
                }
            )
    return out


def read_topic(topic_id: str, post_limit: int = 20) -> Dict[str, Any]:
    """Read a topic and its first ``post_limit`` posts."""
    base = _base_url()
    resp = _http().get(f"{base}/t/{topic_id}.json", headers=_headers())
    resp.raise_for_status()
    data = resp.json() or {}
    posts = (data.get("post_stream", {}) or {}).get("posts", []) or []
    rendered = [
        {
            "post_number": p.get("post_number"),
            "username": p.get("username"),
            "created_at": p.get("created_at"),
            "text": _strip_html(p.get("cooked", "")),
        }
        for p in posts[:post_limit]
    ]
    return {
        "topic_id": topic_id,
        "title": data.get("title") or data.get("fancy_title"),
        "category_id": data.get("category_id"),
        "posts_count": data.get("posts_count"),
        "url": f"{base}/t/{topic_id}",
        "posts": rendered,
    }
