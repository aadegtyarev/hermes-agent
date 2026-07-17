"""Google Docs tools — READ-ONLY.

  * ``gdoc_read``     — read a Google Doc's text by URL or bare document id via
    the Docs API (documents.readonly). Tab-aware: multi-tab documents are read
    in full (every tab + nested child tabs), each tab prefixed with a header.
  * ``gdrive_search`` — full-text search across the user's Drive (content +
    name), returning matching files' id/name/link (drive.readonly). Results are
    cached with a short TTL. No create/update/delete surface exists.
"""
from __future__ import annotations

import os
import re
import threading
import time

from tools.registry import tool_error, tool_result

from . import _gauth

SCOPES = ["https://www.googleapis.com/auth/documents.readonly"]
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

_DOC_ID_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")


def check_available() -> bool:
    try:
        return _gauth.is_configured()
    except Exception:
        return False


def _parse_doc_id(ref: str) -> str:
    ref = (ref or "").strip()
    m = _DOC_ID_RE.search(ref)
    if m:
        return m.group(1)
    # bare id (no slashes/spaces)
    if ref and "/" not in ref and " " not in ref:
        return ref
    return ""


def _text_from_body(body: dict) -> str:
    """Flatten a Docs ``body`` (``{content: [...]}``) to plain text."""
    out = []
    for el in (body.get("content", []) or []):
        para = el.get("paragraph")
        if not para:
            # tables/other structural elements are skipped for a plain-text read
            continue
        for pe in para.get("elements", []) or []:
            run = pe.get("textRun")
            if run and run.get("content"):
                out.append(run["content"])
    return "".join(out)


def _flatten_tabs(tabs: list, depth: int = 0, out: list | None = None) -> list:
    """Walk the tab tree (depth-first, incl. childTabs) into flat records.

    Each record is ``{title, text, level}``. Tabs preserve their on-screen
    order; nested child tabs follow their parent with an increased ``level``.
    """
    if out is None:
        out = []
    for tab in tabs or []:
        props = tab.get("tabProperties", {}) or {}
        body = (tab.get("documentTab", {}) or {}).get("body", {}) or {}
        out.append(
            {
                "title": props.get("title", ""),
                "text": _text_from_body(body),
                "level": depth,
            }
        )
        child = tab.get("childTabs")
        if child:
            _flatten_tabs(child, depth + 1, out)
    return out


def _text_from_doc(doc: dict) -> tuple[str, list]:
    """Return (combined_text, tabs) for a document.

    Tab-aware: for a genuine multi-tab document (Docs' tabs feature) each tab's
    body is flattened and prefixed with a ``# <title>`` header so the plain
    text stays readable; nested tabs are indented by header level. A document
    with a single (default) tab, or a legacy single-body document, returns just
    its plain text with no headers and no tabs list — so the common case is
    unchanged.

    Note: with ``includeTabsContent=True`` the Docs API returns content under
    ``tabs`` for every document (one default tab even when the user never added
    any), leaving the top-level ``body`` empty — hence the single-tab shortcut.
    """
    tabs = _flatten_tabs(doc.get("tabs"))
    if not tabs:
        return _text_from_body(doc.get("body", {}) or {}), []
    if len(tabs) == 1:
        return tabs[0]["text"], []

    parts = []
    for t in tabs:
        header = "#" * (t["level"] + 1)
        title = t["title"] or "(untitled tab)"
        parts.append(f"{header} {title}\n\n{t['text']}".rstrip())
    return "\n\n".join(parts), tabs


GDOC_READ_SCHEMA = {
    "name": "gdoc_read",
    "description": (
        "Read the plain text of a Google Doc by URL or document id. Read-only. "
        "Returns the document title and its text content. Multi-tab documents "
        "are read in full: every tab (and nested child tab) is included, each "
        "prefixed with a '# <tab title>' header, plus a 'tabs' list in the result."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Google Docs URL (https://docs.google.com/document/d/<ID>/...) or a bare document id.",
            },
        },
        "required": ["url"],
    },
}


def handle_gdoc_read(args: dict, **kw) -> str:
    doc_id = _parse_doc_id(str(args.get("url") or ""))
    if not doc_id:
        return tool_error(
            "Pass 'url' as a Google Docs link (…/document/d/<ID>/…) or a bare document id."
        )
    try:
        svc = _gauth.service("docs", "v1", SCOPES)
        # includeTabsContent=True returns every tab's body (Docs' multi-tab
        # feature); without it only the first tab is populated under `body`.
        doc = (
            svc.documents()
            .get(documentId=doc_id, includeTabsContent=True)
            .execute()
        )
    except Exception as e:
        return tool_error(f"Failed to read Google Doc {doc_id}: {e}")
    text, tabs = _text_from_doc(doc)
    payload = {
        "document_id": doc_id,
        "title": doc.get("title", ""),
        "text": text,
    }
    if tabs:
        payload["tab_count"] = len(tabs)
        payload["tabs"] = [
            {"title": t["title"], "level": t["level"]} for t in tabs
        ]
    return tool_result(payload)


# --------------------------------------------------------------------------- #
# gdrive_search — full-text Drive search (read-only) with a small TTL cache
# --------------------------------------------------------------------------- #

# mimeType -> Drive query filter for the `type` param
_TYPE_MIME = {
    "doc": "application/vnd.google-apps.document",
    "sheet": "application/vnd.google-apps.spreadsheet",
    "slides": "application/vnd.google-apps.presentation",
    "pdf": "application/pdf",
    "folder": "application/vnd.google-apps.folder",
}
# mimeType -> friendly label in results
_MIME_LABEL = {v: k for k, v in _TYPE_MIME.items()}

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 25
_CACHE_MAX = 128


def _cache_ttl() -> int:
    """Search-result cache TTL in seconds (env-tunable, 0 disables)."""
    try:
        return max(0, int(os.getenv("GOOGLE_DRIVE_CACHE_TTL", "300")))
    except (TypeError, ValueError):
        return 300


# key -> (expiry_epoch, result_str)
_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


def _cache_get(key):
    ttl = _cache_ttl()
    if ttl <= 0:
        return None
    now = time.time()
    with _CACHE_LOCK:
        # drop expired entries opportunistically
        for k in [k for k, (exp, _) in _CACHE.items() if exp <= now]:
            _CACHE.pop(k, None)
        hit = _CACHE.get(key)
        return hit[1] if hit else None


def _cache_put(key, value: str) -> None:
    ttl = _cache_ttl()
    if ttl <= 0:
        return
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            # evict the soonest-to-expire entry to bound memory
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest, None)
        _CACHE[key] = (time.time() + ttl, value)


def _escape_q(term: str) -> str:
    """Escape a value for a Drive query string literal (backslash then quote)."""
    return term.replace("\\", "\\\\").replace("'", "\\'")


def _mime_label(mime: str) -> str:
    return _MIME_LABEL.get(mime, mime)


GDRIVE_SEARCH_SCHEMA = {
    "name": "gdrive_search",
    "description": (
        "Full-text search across the user's Google Drive — matches the text "
        "content AND the name of files (Docs, Sheets, PDFs, …). Use it to find "
        "documents by what they say, e.g. what the instructions say about a "
        "topic, before reading one with gdoc_read / gsheet_read. Read-only. "
        "Returns matching files with name, id, type and a link. Results are "
        "cached briefly (TTL)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Free-text search phrase; matched against file content and name.",
            },
            "type": {
                "type": "string",
                "enum": ["any", "doc", "sheet", "slides", "pdf", "folder"],
                "description": "Restrict to a file type. Default 'any'.",
            },
            "limit": {
                "type": "integer",
                "description": f"Max results (1-{_MAX_LIMIT}, default {_DEFAULT_LIMIT}).",
            },
        },
        "required": ["query"],
    },
}


def handle_gdrive_search(args: dict, **kw) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return tool_error("Pass 'query' — a free-text phrase to search for in Drive.")
    ftype = str(args.get("type") or "any").strip().lower()
    if ftype and ftype != "any" and ftype not in _TYPE_MIME:
        return tool_error(
            f"Unknown 'type' {ftype!r}. Use one of: any, {', '.join(_TYPE_MIME)}."
        )
    try:
        limit = int(args.get("limit") or _DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(_MAX_LIMIT, limit))

    cache_key = (query, ftype, limit)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    q_parts = [f"fullText contains '{_escape_q(query)}'", "trashed = false"]
    if ftype and ftype != "any":
        q_parts.append(f"mimeType = '{_TYPE_MIME[ftype]}'")
    q = " and ".join(q_parts)

    try:
        svc = _gauth.service("drive", "v3", DRIVE_SCOPES)
        resp = (
            svc.files()
            .list(
                q=q,
                pageSize=limit,
                fields=(
                    "files(id,name,mimeType,modifiedTime,webViewLink,"
                    "owners(emailAddress,displayName))"
                ),
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                corpora="allDrives",
            )
            .execute()
        )
    except Exception as e:
        return tool_error(f"Drive search failed for {query!r}: {e}")

    results = []
    for f in resp.get("files", []) or []:
        owners = [
            o.get("emailAddress") or o.get("displayName")
            for o in (f.get("owners") or [])
            if o.get("emailAddress") or o.get("displayName")
        ]
        results.append(
            {
                "id": f.get("id", ""),
                "name": f.get("name", ""),
                "type": _mime_label(f.get("mimeType", "")),
                "modified": f.get("modifiedTime", ""),
                "owners": owners,
                "url": f.get("webViewLink", ""),
            }
        )

    out = tool_result(
        {
            "query": query,
            "type": ftype,
            "count": len(results),
            "results": results,
            "hint": "Open a hit with gdoc_read (doc) or gsheet_read (sheet) using its url or id.",
        }
    )
    _cache_put(cache_key, out)
    return out
