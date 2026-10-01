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


def _text_from_body(body: dict, mark_suggestions: bool = False) -> str:
    """Flatten a Docs ``body`` (``{content: [...]}``) to plain text.

    ``mark_suggestions``: when the document was fetched with
    ``suggestionsViewMode=SUGGESTIONS_INLINE`` (see ``handle_gdoc_read``'s
    ``include_suggestions`` param), each text run MAY carry
    ``suggestedInsertionIds``/``suggestedDeletionIds`` — Suggesting-mode
    proposals that haven't been accepted/rejected yet. Wrap those runs in
    ``⟪+...⟫``/``⟪-...⟫`` so a plain-text read doesn't silently present a
    pending suggestion as already-accepted content. The Docs API does not
    expose WHO made a suggestion here (unlike comments, where the author is
    directly available) — only that the range is suggested and which kind.
    """
    out = []
    for el in (body.get("content", []) or []):
        para = el.get("paragraph")
        if not para:
            # tables/other structural elements are skipped for a plain-text read
            continue
        for pe in para.get("elements", []) or []:
            run = pe.get("textRun")
            if not run or not run.get("content"):
                continue
            content = run["content"]
            if mark_suggestions:
                # Independent checks, not elif: a run proposed for deletion by
                # one suggester and insertion by another (replace-by-suggestion)
                # carries both id lists — reporting only one would hide half
                # of what's actually pending.
                if run.get("suggestedDeletionIds"):
                    content = f"⟪-{content}⟫"
                if run.get("suggestedInsertionIds"):
                    content = f"⟪+{content}⟫"
            out.append(content)
    return "".join(out)


def _flatten_tabs(tabs: list, depth: int = 0, out: list | None = None,
                   mark_suggestions: bool = False) -> list:
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
                "text": _text_from_body(body, mark_suggestions=mark_suggestions),
                "level": depth,
            }
        )
        child = tab.get("childTabs")
        if child:
            _flatten_tabs(child, depth + 1, out, mark_suggestions=mark_suggestions)
    return out


def _text_from_doc(doc: dict, mark_suggestions: bool = False) -> tuple[str, list]:
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
    tabs = _flatten_tabs(doc.get("tabs"), mark_suggestions=mark_suggestions)
    if not tabs:
        return _text_from_body(doc.get("body", {}) or {}, mark_suggestions=mark_suggestions), []
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
        "prefixed with a '# <tab title>' header, plus a 'tabs' list in the result. "
        "Pass include_suggestions=true to also see pending Suggesting-mode edits "
        "(marked inline as ⟪+inserted⟫/⟪-deleted⟫, not yet accepted/rejected) — "
        "without it, suggested text reads as if already accepted. Use gdoc_comments "
        "for sidebar comment threads, which this does not include."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Google Docs URL (https://docs.google.com/document/d/<ID>/...) or a bare document id.",
            },
            "include_suggestions": {
                "type": "boolean",
                "description": "Mark pending Suggesting-mode insertions/deletions inline instead of reading as already-accepted text (default: false).",
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
    mark_suggestions = bool(args.get("include_suggestions"))
    try:
        svc = _gauth.service("docs", "v1", SCOPES)
        # includeTabsContent=True returns every tab's body (Docs' multi-tab
        # feature); without it only the first tab is populated under `body`.
        # suggestionsViewMode=SUGGESTIONS_INLINE keeps pending suggestions in
        # the response (with suggestedInsertionIds/suggestedDeletionIds on
        # their text runs) instead of previewing as already-accepted/rejected.
        #
        # Passed ONLY when actually requested: per the Docs API's
        # SuggestionsViewMode reference, SUGGESTIONS_INLINE returns an error
        # for a caller who only has view-only access to the document (no
        # permission to see suggested changes) — a real regression on the
        # default, non-opt-in path if sent unconditionally. Omitting the
        # param falls back to DEFAULT_FOR_CURRENT_ACCESS, which degrades
        # gracefully for a viewer the same way this tool always behaved
        # before include_suggestions existed.
        get_kwargs = {"documentId": doc_id, "includeTabsContent": True}
        if mark_suggestions:
            get_kwargs["suggestionsViewMode"] = "SUGGESTIONS_INLINE"
        doc = svc.documents().get(**get_kwargs).execute()
    except Exception as e:
        return tool_error(f"Failed to read Google Doc {doc_id}: {e}")
    text, tabs = _text_from_doc(doc, mark_suggestions=mark_suggestions)
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


# --------------------------------------------------------------------------- #
# gdoc_comments — sidebar comment threads (Drive Comments API), read-only
# --------------------------------------------------------------------------- #

GDOC_COMMENTS_SCHEMA = {
    "name": "gdoc_comments",
    "description": (
        "Read sidebar comment threads on a Google Doc — reviewer feedback that "
        "gdoc_read's plain-text export never includes (comments are a separate "
        "Drive feature, not part of the document body). Each comment includes "
        "its author, the text it was attached to, and any reply thread. "
        "Read-only. Use gdoc_read(include_suggestions=true) instead for "
        "Suggesting-mode inline edits, which are a different mechanism."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Google Docs URL (https://docs.google.com/document/d/<ID>/...) or a bare document id.",
            },
            "include_resolved": {
                "type": "boolean",
                "description": "Include comment threads already marked resolved (default: false — only open ones).",
            },
        },
        "required": ["url"],
    },
}


def handle_gdoc_comments(args: dict, **kw) -> str:
    doc_id = _parse_doc_id(str(args.get("url") or ""))
    if not doc_id:
        return tool_error(
            "Pass 'url' as a Google Docs link (…/document/d/<ID>/…) or a bare document id."
        )
    include_resolved = bool(args.get("include_resolved"))
    _MAX_PAGES = 10  # 10 * 100 = 1000 comments — a sane upper bound, not a real limit
    raw_comments: list[dict] = []
    try:
        svc = _gauth.service("drive", "v3", DRIVE_SCOPES)
        page_token = None
        for _ in range(_MAX_PAGES):
            list_kwargs = {
                "fileId": doc_id,
                "pageSize": 100,
                "fields": (
                    "nextPageToken,"
                    "comments(id,content,author(displayName),createdTime,"
                    "resolved,quotedFileContent(value),"
                    "replies(content,author(displayName),createdTime))"
                ),
                "includeDeleted": False,
            }
            if page_token:
                list_kwargs["pageToken"] = page_token
            resp = svc.comments().list(**list_kwargs).execute()
            raw_comments.extend(resp.get("comments", []) or [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except Exception as e:
        return tool_error(f"Failed to read comments for {doc_id}: {e}")

    comments = []
    for c in raw_comments:
        if c.get("resolved") and not include_resolved:
            continue
        comments.append({
            "id": c.get("id", ""),
            "author": (c.get("author") or {}).get("displayName", ""),
            "created": c.get("createdTime", ""),
            "resolved": bool(c.get("resolved")),
            "quoted_text": (c.get("quotedFileContent") or {}).get("value", ""),
            "text": c.get("content", ""),
            "replies": [
                {
                    "author": (r.get("author") or {}).get("displayName", ""),
                    "created": r.get("createdTime", ""),
                    "text": r.get("content", ""),
                }
                for r in (c.get("replies") or [])
            ],
        })
    return tool_result({"document_id": doc_id, "count": len(comments), "comments": comments})
