"""curl + Jina Reader web extract — plugin form.

Subclasses :class:`agent.web_search_provider.WebSearchProvider`. A free
``web_extract`` backend with **zero local browser footprint**, built for
hosts that cannot run headless Chromium (e.g. a 512 MB container):

  - **Tier 1** — fetch raw HTML over the configured proxy (``httpx`` with
    ``trust_env`` → ``HTTP(S)_PROXY``) and extract readable markdown using
    ``lxml`` only. No trafilatura / bs4 / readability needed — they are not
    installed in the lean image; ``lxml`` is.
  - **Tier 2** — when Tier 1 yields thin content (a JS-rendered SPA shell)
    or fails, fall back to **Jina AI Reader** (``https://r.jina.ai/<url>``),
    which renders JavaScript on Jina's servers and returns clean markdown.
    Nothing renders locally.

Search is not implemented (``supports_search() == False``); pair with the
``ddgs`` / ``searxng`` search backend for ``web_search``.

No API key required. Optional ``JINA_API_KEY`` raises the Reader rate limit.

Config keys this provider responds to::

    web:
      extract_backend: "curl-jina"   # explicit per-capability
      backend: "curl-jina"           # shared fallback

The per-URL result shape matches the Firecrawl provider exactly so the
``web_extract`` tool wrapper needs no changes::

    {"url", "title", "content", "raw_content", "metadata"}   # or {"error"}
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
from typing import Any, Dict, List, Tuple

from agent.web_search_provider import WebSearchProvider

try:  # policy gate is part of the host; degrade gracefully if it moves
    from tools.website_policy import check_website_access
except Exception:  # noqa: BLE001
    def check_website_access(url: str, config_path: Any = None):  # type: ignore
        return None

logger = logging.getLogger(__name__)

# Tier-1 markdown shorter than this (chars) is treated as "thin" — likely a
# JS-rendered shell — and re-fetched via Jina Reader.
_TIER1_MIN_CHARS = 500
_FETCH_TIMEOUT = 20.0
_JINA_TIMEOUT = 60.0
_JINA_ENDPOINT = "https://r.jina.ai/"
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _interrupted() -> bool:
    """Best-effort interrupt check; never raises if the host module changes."""
    try:
        from tools.interrupt import is_interrupted

        return bool(is_interrupted())
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Tier 1 — lxml-only HTML -> markdown (no trafilatura/bs4)
# ---------------------------------------------------------------------------

_DROP_TAGS = (
    "script", "style", "noscript", "template", "svg", "nav", "header",
    "footer", "aside", "form", "iframe", "button", "input", "select",
    "figure", "picture", "video", "audio", "canvas",
)
_HEADINGS = {
    "h1": "# ", "h2": "## ", "h3": "### ",
    "h4": "#### ", "h5": "##### ", "h6": "###### ",
}
_BLOCK_CONTAINERS = {"div", "section", "article", "main", "body"}
_BLOCK_TAGS = (
    set(_HEADINGS) | {"p", "ul", "ol", "pre", "blockquote", "table", "hr"}
    | _BLOCK_CONTAINERS
)


def _tag(el: Any) -> str:
    return el.tag if isinstance(el.tag, str) else ""


def _inline(el: Any) -> str:
    """Serialize inline content of *el*, preserving links / emphasis / code."""
    out: List[str] = []
    if el.text:
        out.append(el.text)
    for child in el:
        ct = _tag(child)
        inner = _inline(child)
        if ct == "a":
            href = (child.get("href") or "").strip()
            txt = inner.strip()
            out.append(f"[{txt}]({href})" if href and txt else txt)
        elif ct == "code":
            out.append(f"`{inner.strip()}`")
        elif ct in ("strong", "b"):
            out.append(f"**{inner.strip()}**")
        elif ct in ("em", "i"):
            out.append(f"*{inner.strip()}*")
        elif ct == "br":
            out.append("\n")
        else:
            out.append(inner)
        if child.tail:
            out.append(child.tail)
    return "".join(out)


def _has_block_child(el: Any) -> bool:
    return any(_tag(c) in _BLOCK_TAGS for c in el)


def _blocks(el: Any) -> str:
    """Recursively serialize block-level content of *el* to markdown."""
    parts: List[str] = []
    for node in el:
        t = _tag(node)
        if t in _HEADINGS:
            txt = _inline(node).strip()
            if txt:
                parts.append(_HEADINGS[t] + txt)
        elif t == "p":
            txt = _inline(node).strip()
            if txt:
                parts.append(txt)
        elif t in ("ul", "ol"):
            items: List[str] = []
            for i, li in enumerate(node.findall("li")):
                bullet = "- " if t == "ul" else f"{i + 1}. "
                line = _inline(li).strip()
                if line:
                    items.append(bullet + line)
            if items:
                parts.append("\n".join(items))
        elif t == "pre":
            code = node.text_content().strip("\n")
            if code.strip():
                parts.append("```\n" + code + "\n```")
        elif t == "blockquote":
            txt = _inline(node).strip()
            if txt:
                parts.append("> " + txt.replace("\n", "\n> "))
        elif t == "hr":
            parts.append("---")
        elif t in _BLOCK_CONTAINERS or t == "":
            inner = _blocks(node) if _has_block_child(node) else _inline(node).strip()
            if inner:
                parts.append(inner)
        else:
            inner = _blocks(node) if _has_block_child(node) else _inline(node).strip()
            if inner:
                parts.append(inner)
    return "\n\n".join(p for p in parts if p.strip())


def _extract_with_lxml(html: str) -> Tuple[str, str]:
    """Return ``(title, markdown)`` extracted from raw HTML using lxml only."""
    import lxml.html as LH
    from lxml import etree

    doc = LH.fromstring(html)

    title = ""
    t = doc.xpath("//title/text()")
    if t:
        title = t[0].strip()
    if not title:
        og = doc.xpath("//meta[@property='og:title']/@content")
        if og:
            title = og[0].strip()

    etree.strip_elements(doc, *_DROP_TAGS, with_tail=False)
    for comment in doc.xpath("//comment()"):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)

    root = None
    for xp in ("//main", "//article", "//*[@role='main']",
               "//div[@id='content']", "//div[@id='main']"):
        found = doc.xpath(xp)
        if found:
            root = found[0]
            break
    if root is None:
        body = doc.xpath("//body")
        root = body[0] if body else doc

    try:
        md = _blocks(root)
    except Exception:  # noqa: BLE001 — never let the walker kill extraction
        md = root.text_content()

    if not title:
        h1 = root.xpath(".//h1//text()")
        if h1:
            title = "".join(h1).strip()

    md = "\n".join(line.rstrip() for line in md.splitlines())
    while "\n\n\n" in md:
        md = md.replace("\n\n\n", "\n\n")
    return title, md.strip()


# ---------------------------------------------------------------------------
# HTTP fetch (sync; called from threads). Proxy comes from env via trust_env.
# ---------------------------------------------------------------------------


def _fetch_html(url: str) -> Tuple[int, str, str]:
    """Fetch raw HTML through the env proxy. Returns ``(status, final_url, text)``."""
    import httpx

    headers = {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml"}
    with httpx.Client(
        follow_redirects=True, timeout=_FETCH_TIMEOUT,
        headers=headers, trust_env=True,
    ) as client:
        resp = client.get(url)
        return resp.status_code, str(resp.url), resp.text


def _fetch_jina(url: str) -> Tuple[str, str]:
    """Fetch JS-rendered markdown via Jina Reader. Returns ``(title, markdown)``."""
    import httpx

    headers = {"Accept": "application/json", "X-Return-Format": "markdown"}
    key = os.getenv("JINA_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    with httpx.Client(
        follow_redirects=True, timeout=_JINA_TIMEOUT,
        headers=headers, trust_env=True,
    ) as client:
        resp = client.get(_JINA_ENDPOINT + url)
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001 — non-JSON reader response
            return "", resp.text.strip()
    payload = data.get("data") if isinstance(data, dict) else None
    if isinstance(payload, dict):
        return (payload.get("title") or "").strip(), (payload.get("content") or "").strip()
    return "", ""


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class CurlJinaWebSearchProvider(WebSearchProvider):
    """Free, browser-free extract: lxml over curl, Jina Reader as fallback."""

    @property
    def name(self) -> str:
        return "curl-jina"

    @property
    def display_name(self) -> str:
        return "curl + Jina Reader"

    def is_available(self) -> bool:
        """True when httpx + lxml are importable (no network, no API key)."""
        return all(importlib.util.find_spec(m) for m in ("httpx", "lxml"))

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    def _blocked_result(self, url: str, blocked: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "url": url,
            "title": "",
            "content": "",
            "raw_content": "",
            "error": blocked.get("message", "Blocked by website policy"),
            "blocked_by_policy": {
                "host": blocked.get("host"),
                "rule": blocked.get("rule"),
                "source": blocked.get("source"),
            },
        }

    async def _extract_one(self, url: str) -> Dict[str, Any]:
        blocked = check_website_access(url)
        if blocked:
            logger.info("Blocked web_extract for %s by rule %s",
                        blocked.get("host"), blocked.get("rule"))
            return self._blocked_result(url, blocked)

        status, final_url, html, title, md = 0, url, "", "", ""

        # Tier 1 — curl + lxml
        try:
            status, final_url, html = await asyncio.wait_for(
                asyncio.to_thread(_fetch_html, url), timeout=_FETCH_TIMEOUT + 5,
            )
        except Exception as exc:  # noqa: BLE001 — fall through to Jina
            logger.info("curl-jina Tier1 fetch failed for %s: %s", url, exc)

        # Re-check policy after any redirect
        final_blocked = check_website_access(final_url)
        if final_blocked:
            logger.info("Blocked redirected web_extract for %s by rule %s",
                        final_blocked.get("host"), final_blocked.get("rule"))
            return self._blocked_result(final_url, final_blocked)

        if html and 200 <= status < 300:
            try:
                title, md = await asyncio.to_thread(_extract_with_lxml, html)
            except Exception as exc:  # noqa: BLE001
                logger.info("curl-jina lxml extract failed for %s: %s", url, exc)

        used = "lxml"

        # Tier 2 — Jina Reader renders JS remotely when Tier 1 is thin/empty
        if len(md.strip()) < _TIER1_MIN_CHARS:
            try:
                jtitle, jmd = await asyncio.wait_for(
                    asyncio.to_thread(_fetch_jina, final_url or url),
                    timeout=_JINA_TIMEOUT + 5,
                )
                if len(jmd.strip()) > len(md.strip()):
                    md, title, used = jmd, (jtitle or title), "jina"
            except Exception as exc:  # noqa: BLE001
                logger.info("curl-jina Tier2 (jina) failed for %s: %s", url, exc)

        if not md.strip():
            return {
                "url": final_url or url, "title": title,
                "content": "", "raw_content": "",
                "error": "No content extracted (curl + Jina both empty)",
            }

        return {
            "url": final_url or url,
            "title": title,
            "content": md,
            "raw_content": md,
            "metadata": {
                "sourceURL": final_url or url,
                "title": title,
                "extractor": used,
            },
        }

    async def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Extract content from one or more URLs. ``format`` kwarg is ignored
        (always returns markdown). Per-URL failures become ``error`` items
        rather than raising."""
        if _interrupted():
            return [{"url": u, "error": "Interrupted", "title": ""} for u in urls]

        results: List[Dict[str, Any]] = []
        for url in urls:
            if _interrupted():
                results.append({"url": url, "error": "Interrupted", "title": ""})
                continue
            try:
                results.append(await self._extract_one(url))
            except Exception as exc:  # noqa: BLE001
                logger.warning("curl-jina extract error for %s: %s", url, exc)
                results.append({
                    "url": url, "title": "", "content": "",
                    "raw_content": "", "error": str(exc),
                })
        return results

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "curl + Jina Reader",
            "badge": "free · no browser",
            "tag": (
                "Tier 1 fetches HTML and extracts via lxml; thin/JS pages fall "
                "back to Jina Reader (renders remotely). No local browser."
            ),
            "env_vars": [
                {
                    "key": "JINA_API_KEY",
                    "prompt": (
                        "Jina Reader API key (optional — raises rate limit; "
                        "blank = anonymous)"
                    ),
                    "url": "https://jina.ai/reader/",
                },
            ],
        }
