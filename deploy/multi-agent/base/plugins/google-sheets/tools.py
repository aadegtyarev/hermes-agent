"""Google Sheets tools — READ-ONLY (gsheet_list_sheets, gsheet_read).

Reads spreadsheet metadata and cell ranges via the Sheets API with the
spreadsheets.readonly scope. No write surface exists.
"""
from __future__ import annotations

import re

from tools.registry import tool_error, tool_result

from . import _gauth

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")


def check_available() -> bool:
    try:
        return _gauth.is_configured()
    except Exception:
        return False


def _parse_sheet_id(ref: str) -> str:
    ref = (ref or "").strip()
    m = _SHEET_ID_RE.search(ref)
    if m:
        return m.group(1)
    if ref and "/" not in ref and " " not in ref:
        return ref
    return ""


GSHEET_LIST_SHEETS_SCHEMA = {
    "name": "gsheet_list_sheets",
    "description": (
        "List the tabs (sheets) of a Google Spreadsheet by URL or id. Read-only. "
        "Returns each sheet's title, index and dimensions — call before gsheet_read "
        "to learn valid sheet names for the 'range' argument."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Google Sheets URL (…/spreadsheets/d/<ID>/…) or a bare spreadsheet id.",
            },
        },
        "required": ["url"],
    },
}

GSHEET_READ_SCHEMA = {
    "name": "gsheet_read",
    "description": (
        "Read cell values from a Google Spreadsheet by URL or id. Read-only. "
        "Returns rows as a list of lists. Optionally restrict to an A1 'range' "
        "(e.g. 'Sheet1!A1:D50'); omit to read the first sheet."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Google Sheets URL (…/spreadsheets/d/<ID>/…) or a bare spreadsheet id.",
            },
            "range": {
                "type": "string",
                "description": "Optional A1 range, e.g. 'Sheet1!A1:D50' or 'Sheet1'. Omit for the first sheet.",
            },
        },
        "required": ["url"],
    },
}


def handle_gsheet_list_sheets(args: dict, **kw) -> str:
    sid = _parse_sheet_id(str(args.get("url") or ""))
    if not sid:
        return tool_error(
            "Pass 'url' as a Google Sheets link (…/spreadsheets/d/<ID>/…) or a bare id."
        )
    try:
        svc = _gauth.service("sheets", "v4", SCOPES)
        meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    except Exception as e:
        return tool_error(f"Failed to open spreadsheet {sid}: {e}")
    sheets = []
    for s in meta.get("sheets", []) or []:
        sp = s.get("properties", {}) or {}
        grid = sp.get("gridProperties", {}) or {}
        sheets.append(
            {
                "title": sp.get("title", ""),
                "sheet_id": sp.get("sheetId"),
                "index": sp.get("index"),
                "rows": grid.get("rowCount"),
                "cols": grid.get("columnCount"),
            }
        )
    return tool_result(
        {"spreadsheet_id": sid, "title": meta.get("properties", {}).get("title", ""), "sheets": sheets}
    )


def handle_gsheet_read(args: dict, **kw) -> str:
    sid = _parse_sheet_id(str(args.get("url") or ""))
    if not sid:
        return tool_error(
            "Pass 'url' as a Google Sheets link (…/spreadsheets/d/<ID>/…) or a bare id."
        )
    rng = str(args.get("range") or "").strip()
    try:
        svc = _gauth.service("sheets", "v4", SCOPES)
        if not rng:
            meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
            first = (meta.get("sheets") or [{}])[0].get("properties", {}) or {}
            rng = first.get("title", "Sheet1")
        resp = (
            svc.spreadsheets()
            .values()
            .get(spreadsheetId=sid, range=rng)
            .execute()
        )
    except Exception as e:
        return tool_error(f"Failed to read spreadsheet {sid} range '{rng}': {e}")
    values = resp.get("values", [])
    return tool_result(
        {"spreadsheet_id": sid, "range": resp.get("range", rng), "row_count": len(values), "values": values}
    )
