"""Tests for the Discourse (read-only) plugin: registration, gating, handlers."""
import json

from plugins.discourse import client, tools
from plugins.discourse import register


def test_register_wires_two_readonly_tools():
    calls = []

    class Ctx:
        def register_tool(self, **kw):
            calls.append(kw)

    register(Ctx())
    names = {c["name"] for c in calls}
    assert names == {"discourse_search", "discourse_read_topic"}
    assert all(c["toolset"] == "discourse" for c in calls)
    assert all(callable(c["check_fn"]) for c in calls)


def test_check_available_follows_configuration(monkeypatch):
    monkeypatch.setattr(client, "is_configured", lambda: True)
    assert tools.check_discourse_available() is True
    monkeypatch.setattr(client, "is_configured", lambda: False)
    assert tools.check_discourse_available() is False


def test_search_handler_returns_results(monkeypatch):
    monkeypatch.setattr(
        client, "search",
        lambda q, max_results=10: [{"topic_id": 7, "title": "Modbus CRC", "url": "x"}],
    )
    out = json.loads(tools.handle_discourse_search({"query": "modbus"}))
    assert out["count"] == 1
    assert out["results"][0]["topic_id"] == 7


def test_search_handler_requires_query():
    out = json.loads(tools.handle_discourse_search({"query": "  "}))
    assert "error" in out


def test_search_handler_surfaces_errors(monkeypatch):
    def boom(q, max_results=10):
        raise RuntimeError("503")

    monkeypatch.setattr(client, "search", boom)
    out = json.loads(tools.handle_discourse_search({"query": "x"}))
    assert "error" in out and "503" in out["error"]


def test_read_topic_handler(monkeypatch):
    monkeypatch.setattr(
        client, "read_topic",
        lambda tid, post_limit=20: {"topic_id": tid, "title": "T", "posts": []},
    )
    out = json.loads(tools.handle_discourse_read_topic({"topic_id": "42"}))
    assert out["topic_id"] == "42"


def test_read_topic_requires_id():
    out = json.loads(tools.handle_discourse_read_topic({}))
    assert "error" in out


def test_strip_html_cleans_and_truncates():
    assert client._strip_html("<p>hi <b>there</b></p>") == "hi there"
    long = client._strip_html("<p>" + "a" * 5000 + "</p>", limit=100)
    assert long.endswith("[…]") and len(long) <= 110
