"""Tests for deploy/multi-agent/base/plugins/google-docs.

Focus: gdoc_read's new include_suggestions markup (Suggesting-mode inline
edits are a different mechanism from comments and are invisible to a plain
gdoc_read call unless explicitly requested) and the new gdoc_comments tool
(Drive Comments API — sidebar threads, also invisible to gdoc_read). Both
built from Google API documentation, not verified against a live document —
smoke-test against a real Suggesting-mode edit and a real comment thread
before relying on this in production.

Loaded via importlib (hyphenated directory name, not an importable package),
same pattern as the telegram-context plugin tests.
"""
from __future__ import annotations

import importlib.util
import itertools
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PLUGIN_DIR = (
    Path(__file__).resolve().parents[2]
    / "deploy" / "multi-agent" / "base" / "plugins" / "google-docs"
)
_counter = itertools.count()


@pytest.fixture
def tools_mod():
    pkg_name = f"google_docs_under_test_{next(_counter)}"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(_PLUGIN_DIR)]
    sys.modules[pkg_name] = pkg

    gauth_spec = importlib.util.spec_from_file_location(
        f"{pkg_name}._gauth", _PLUGIN_DIR / "_gauth.py"
    )
    gauth_mod = importlib.util.module_from_spec(gauth_spec)
    sys.modules[f"{pkg_name}._gauth"] = gauth_mod
    gauth_spec.loader.exec_module(gauth_mod)

    tools_spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.tools", _PLUGIN_DIR / "tools.py"
    )
    mod = importlib.util.module_from_spec(tools_spec)
    mod.__package__ = pkg_name
    sys.modules[f"{pkg_name}.tools"] = mod
    tools_spec.loader.exec_module(mod)

    yield mod

    for key in list(sys.modules):
        if key.startswith(pkg_name):
            del sys.modules[key]


# ── _parse_doc_id ────────────────────────────────────────────────────────────

def test_parse_doc_id_from_full_url(tools_mod):
    assert tools_mod._parse_doc_id(
        "https://docs.google.com/document/d/abc123XYZ-_/edit?tab=t.0"
    ) == "abc123XYZ-_"


def test_parse_doc_id_from_bare_id(tools_mod):
    assert tools_mod._parse_doc_id("abc123XYZ-_") == "abc123XYZ-_"


def test_parse_doc_id_rejects_garbage(tools_mod):
    assert tools_mod._parse_doc_id("not a url or id") == ""
    assert tools_mod._parse_doc_id("") == ""


# ── _text_from_body: suggestion markup ──────────────────────────────────────

def _body_with_runs(*runs: dict) -> dict:
    return {"content": [{"paragraph": {"elements": [{"textRun": r} for r in runs]}}]}


def test_suggestions_read_as_accepted_text_by_default(tools_mod):
    body = _body_with_runs(
        {"content": "normal "},
        {"content": "inserted ", "suggestedInsertionIds": ["s1"]},
        {"content": "text"},
    )
    assert tools_mod._text_from_body(body, mark_suggestions=False) == "normal inserted text"


def test_suggested_insertion_marked_when_requested(tools_mod):
    body = _body_with_runs(
        {"content": "normal "},
        {"content": "inserted ", "suggestedInsertionIds": ["s1"]},
        {"content": "text"},
    )
    assert tools_mod._text_from_body(body, mark_suggestions=True) == "normal ⟪+inserted ⟫text"


def test_suggested_deletion_marked_when_requested(tools_mod):
    body = _body_with_runs(
        {"content": "keep "},
        {"content": "remove me ", "suggestedDeletionIds": ["s2"]},
        {"content": "rest"},
    )
    assert tools_mod._text_from_body(body, mark_suggestions=True) == "keep ⟪-remove me ⟫rest"


def test_unmarked_runs_unaffected_by_suggestion_mode(tools_mod):
    body = _body_with_runs({"content": "plain text, nothing pending"})
    assert tools_mod._text_from_body(body, mark_suggestions=True) == "plain text, nothing pending"


def test_run_with_both_insertion_and_deletion_ids_marks_both(tools_mod):
    """A run proposed for deletion by one suggester and insertion by another
    (replace-by-suggestion) carries BOTH id lists — reporting only one
    (the original code used elif) would silently hide half of what's
    actually pending review."""
    body = _body_with_runs({
        "content": "replaced text",
        "suggestedInsertionIds": ["s1"],
        "suggestedDeletionIds": ["s2"],
    })
    result = tools_mod._text_from_body(body, mark_suggestions=True)
    assert "⟪-" in result and "⟪+" in result and "replaced text" in result


# ── handle_gdoc_read: suggestionsViewMode wiring ────────────────────────────

def test_gdoc_read_requests_suggestions_inline_view_mode_only_when_asked(tools_mod, monkeypatch):
    """suggestionsViewMode must be OMITTED by default, not sent as
    SUGGESTIONS_INLINE unconditionally — the Docs API returns an error for
    that mode when the caller only has view-only access to the document, a
    real regression on the default (non-opt-in) read path if sent always."""
    fake_docs = MagicMock()
    fake_docs.documents().get().execute.return_value = {
        "title": "Draft",
        "body": {"content": []},
    }
    monkeypatch.setattr(tools_mod._gauth, "service", lambda *a, **k: fake_docs)

    tools_mod.handle_gdoc_read({"url": "abc123"})
    _, kwargs = fake_docs.documents().get.call_args
    assert "suggestionsViewMode" not in kwargs

    tools_mod.handle_gdoc_read({"url": "abc123", "include_suggestions": True})
    _, kwargs = fake_docs.documents().get.call_args
    assert kwargs.get("suggestionsViewMode") == "SUGGESTIONS_INLINE"


def test_gdoc_read_marks_suggestions_when_requested(tools_mod, monkeypatch):
    import json

    fake_docs = MagicMock()
    fake_docs.documents().get().execute.return_value = {
        "title": "Draft",
        "body": _body_with_runs(
            {"content": "base text "},
            {"content": "maybe add this", "suggestedInsertionIds": ["s1"]},
        ),
    }
    monkeypatch.setattr(tools_mod._gauth, "service", lambda *a, **k: fake_docs)

    result = json.loads(tools_mod.handle_gdoc_read({"url": "abc123", "include_suggestions": True}))

    assert "⟪+maybe add this⟫" in result["text"]


def test_gdoc_read_missing_url_is_an_error(tools_mod):
    import json

    result = json.loads(tools_mod.handle_gdoc_read({}))
    assert "error" in result


# ── handle_gdoc_comments ─────────────────────────────────────────────────────

def _fake_comments_response(*comments: dict) -> dict:
    return {"comments": list(comments)}


def test_gdoc_comments_maps_author_text_and_replies(tools_mod, monkeypatch):
    import json

    fake_drive = MagicMock()
    fake_drive.comments().list().execute.return_value = _fake_comments_response({
        "id": "c1",
        "author": {"displayName": "Partner Ivan"},
        "createdTime": "2026-01-01T00:00:00Z",
        "resolved": False,
        "quotedFileContent": {"value": "the quoted sentence"},
        "content": "This number looks wrong",
        "replies": [
            {"author": {"displayName": "Journalist"}, "createdTime": "2026-01-02T00:00:00Z",
             "content": "Fixed, thanks"},
        ],
    })
    monkeypatch.setattr(tools_mod._gauth, "service", lambda *a, **k: fake_drive)

    result = json.loads(tools_mod.handle_gdoc_comments({"url": "abc123"}))

    assert result["count"] == 1
    c = result["comments"][0]
    assert c["author"] == "Partner Ivan"
    assert c["quoted_text"] == "the quoted sentence"
    assert c["text"] == "This number looks wrong"
    assert c["replies"] == [{"author": "Journalist", "created": "2026-01-02T00:00:00Z", "text": "Fixed, thanks"}]


def test_gdoc_comments_excludes_resolved_by_default(tools_mod, monkeypatch):
    import json

    fake_drive = MagicMock()
    fake_drive.comments().list().execute.return_value = _fake_comments_response(
        {"id": "c1", "content": "open thread", "resolved": False, "author": {}, "replies": []},
        {"id": "c2", "content": "closed thread", "resolved": True, "author": {}, "replies": []},
    )
    monkeypatch.setattr(tools_mod._gauth, "service", lambda *a, **k: fake_drive)

    result = json.loads(tools_mod.handle_gdoc_comments({"url": "abc123"}))

    assert result["count"] == 1
    assert result["comments"][0]["id"] == "c1"


def test_gdoc_comments_includes_resolved_when_requested(tools_mod, monkeypatch):
    import json

    fake_drive = MagicMock()
    fake_drive.comments().list().execute.return_value = _fake_comments_response(
        {"id": "c1", "content": "open thread", "resolved": False, "author": {}, "replies": []},
        {"id": "c2", "content": "closed thread", "resolved": True, "author": {}, "replies": []},
    )
    monkeypatch.setattr(tools_mod._gauth, "service", lambda *a, **k: fake_drive)

    result = json.loads(tools_mod.handle_gdoc_comments({"url": "abc123", "include_resolved": True}))

    assert result["count"] == 2


def test_gdoc_comments_missing_url_is_an_error(tools_mod):
    import json

    result = json.loads(tools_mod.handle_gdoc_comments({}))
    assert "error" in result


def test_gdoc_comments_follows_pagination(tools_mod, monkeypatch):
    """Drive v3 comments.list defaults to a small page size — a doc with
    more open threads than one page was silently truncated with no
    indication before this fix."""
    import json

    page1 = {"nextPageToken": "page2",
             "comments": [{"id": "c1", "content": "first page", "resolved": False, "author": {}, "replies": []}]}
    page2 = {"comments": [{"id": "c2", "content": "second page", "resolved": False, "author": {}, "replies": []}]}

    fake_drive = MagicMock()
    fake_drive.comments().list().execute.side_effect = [page1, page2]
    monkeypatch.setattr(tools_mod._gauth, "service", lambda *a, **k: fake_drive)

    result = json.loads(tools_mod.handle_gdoc_comments({"url": "abc123"}))

    assert result["count"] == 2
    assert {c["id"] for c in result["comments"]} == {"c1", "c2"}


def test_gdoc_comments_pagination_is_bounded(tools_mod, monkeypatch):
    """An endless nextPageToken (buggy/malicious API response) must not hang
    the tool forever — capped at _MAX_PAGES."""
    import json

    def _always_more(*a, **k):
        return {"nextPageToken": "more", "comments": []}

    fake_drive = MagicMock()
    fake_drive.comments().list().execute.side_effect = _always_more
    monkeypatch.setattr(tools_mod._gauth, "service", lambda *a, **k: fake_drive)

    result = json.loads(tools_mod.handle_gdoc_comments({"url": "abc123"}))

    assert result["count"] == 0  # terminates instead of hanging
