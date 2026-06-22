"""Tests for the YouTrack (read+write) plugin: registration, gating, handlers."""
import json

from plugins.youtrack import client, tools
from plugins.youtrack import register


def test_register_wires_four_tools():
    calls = []

    class Ctx:
        def register_tool(self, **kw):
            calls.append(kw)

    register(Ctx())
    names = {c["name"] for c in calls}
    assert names == {
        "youtrack_search", "youtrack_read_issue",
        "youtrack_comment", "youtrack_create_issue",
    }
    assert all(c["toolset"] == "youtrack" for c in calls)


def test_check_available_requires_url_and_token(monkeypatch):
    monkeypatch.setattr(client, "is_configured", lambda: True)
    assert tools.check_youtrack_available() is True
    monkeypatch.setattr(client, "is_configured", lambda: False)
    assert tools.check_youtrack_available() is False


def test_search_handler(monkeypatch):
    monkeypatch.setattr(
        client, "search",
        lambda q, max_results=20: [{"id": "SUP-1", "summary": "x"}],
    )
    out = json.loads(tools.handle_youtrack_search({"query": "project: SUP"}))
    assert out["count"] == 1 and out["results"][0]["id"] == "SUP-1"


def test_read_issue_handler(monkeypatch):
    monkeypatch.setattr(
        client, "read_issue",
        lambda iid: {"id": iid, "summary": "S", "comments": []},
    )
    out = json.loads(tools.handle_youtrack_read_issue({"issue_id": "SUP-9"}))
    assert out["id"] == "SUP-9"


def test_comment_handler_requires_text():
    out = json.loads(tools.handle_youtrack_comment({"issue_id": "SUP-9"}))
    assert "error" in out


def test_comment_handler_writes(monkeypatch):
    seen = {}

    def fake_add(issue_id, text):
        seen["issue_id"] = issue_id
        seen["text"] = text
        return {"issue_id": issue_id, "comment_id": "c1"}

    monkeypatch.setattr(client, "add_comment", fake_add)
    out = json.loads(tools.handle_youtrack_comment({"issue_id": "SUP-9", "text": "hi"}))
    assert out["comment_id"] == "c1"
    assert seen == {"issue_id": "SUP-9", "text": "hi"}


def test_create_issue_handler(monkeypatch):
    monkeypatch.setattr(
        client, "create_issue",
        lambda project, summary, description="": {"id": "SUP-100", "url": "u"},
    )
    out = json.loads(tools.handle_youtrack_create_issue(
        {"project": "SUP", "summary": "Bug"}
    ))
    assert out["id"] == "SUP-100"


def test_create_issue_requires_project_and_summary():
    assert "error" in json.loads(tools.handle_youtrack_create_issue({"summary": "x"}))
    assert "error" in json.loads(tools.handle_youtrack_create_issue({"project": "SUP"}))


def test_render_issue_flattens_fields_and_comments():
    raw = {
        "idReadable": "SUP-5",
        "summary": "Sensor offline",
        "description": "desc",
        "reporter": {"login": "alice"},
        "customFields": [
            {"name": "State", "value": {"name": "Open"}},
            {"name": "Assignee", "value": {"login": "bob"}},
            {"name": "Tags", "value": [{"name": "modbus"}, {"name": "rs485"}]},
        ],
        "comments": [
            {"text": "looking", "author": {"login": "bob"}, "created": 1},
        ],
    }
    rendered = client._render_issue(raw)
    assert rendered["id"] == "SUP-5"
    assert rendered["fields"]["State"] == "Open"
    assert rendered["fields"]["Assignee"] == "bob"
    assert rendered["fields"]["Tags"] == ["modbus", "rs485"]
    assert rendered["comments"][0]["author"] == "bob"
