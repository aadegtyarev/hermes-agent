"""Google Docs tool — READ-ONLY (gdoc_read).

Reads a Google Doc's text by URL or bare document id via the Docs API with the
documents.readonly scope. No create/update/delete surface exists.
"""
from __future__ import annotations

import re

from tools.registry import tool_error, tool_result

from . import _gauth

SCOPES = ["https://www.googleapis.com/auth/documents.readonly"]

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


def _text_from_body(doc: dict) -> str:
    """Flatten the Docs structural content to plain text."""
    out = []
    for el in (doc.get("body", {}).get("content", []) or []):
        para = el.get("paragraph")
        if not para:
            # tables/other structural elements are skipped for a plain-text read
            continue
        for pe in para.get("elements", []) or []:
            run = pe.get("textRun")
            if run and run.get("content"):
                out.append(run["content"])
    return "".join(out)


GDOC_READ_SCHEMA = {
    "name": "gdoc_read",
    "description": (
        "Read the plain text of a Google Doc by URL or document id. Read-only. "
        "Returns the document title and its text content."
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
        doc = svc.documents().get(documentId=doc_id).execute()
    except Exception as e:
        return tool_error(f"Failed to read Google Doc {doc_id}: {e}")
    return tool_result(
        {
            "document_id": doc_id,
            "title": doc.get("title", ""),
            "text": _text_from_body(doc),
        }
    )
