"""Tests for the synchronous image auto-description feature in
deploy/multi-agent/base/plugins/telegram-context/__init__.py's _ingest().

Scope (2026-10-02): describe an image inline, blocking, only when the
message is either a REAL dispatch (uid set — mention/reply/command/DM) or
the chat already counts as an "active dialogue" (store.chat_has_recent_
activity — see test_telegram_context_recent_activity.py for that function's
own tests). Everything else keeps the plain pre-existing cache-path note
untouched. _describe_image_sync itself (the actual vision/LLM call) is
monkeypatched out here — these tests are about the _ingest() gating logic
and message shaping, not the vision call itself.
"""
from __future__ import annotations

import importlib.util
import itertools
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_PLUGIN_DIR = (
    Path(__file__).resolve().parents[2]
    / "deploy" / "multi-agent" / "base" / "plugins" / "telegram-context"
)
_counter = itertools.count()


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.delenv("TELEGRAM_AUTO_DESCRIBE_IMAGES", raising=False)
    monkeypatch.delenv("TELEGRAM_ACTIVE_DIALOGUE_WINDOW_SECONDS", raising=False)

    pkg_name = f"telegram_context_autodescribe_under_test_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(
        pkg_name, _PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(_PLUGIN_DIR)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = pkg_name
    mod.__path__ = [str(_PLUGIN_DIR)]
    sys.modules[pkg_name] = mod
    spec.loader.exec_module(mod)
    mod.store.init()

    yield mod

    for key in list(sys.modules):
        if key == pkg_name or key.startswith(pkg_name + "."):
            del sys.modules[key]


def _message(text, *, chat_id="-100700", from_user_id="222",
             media_urls=None, media_types=None, message_id="1"):
    return SimpleNamespace(
        message_id=message_id,
        text=text,
        source=SimpleNamespace(
            platform="telegram", chat_id=chat_id, user_id=from_user_id,
            user_name="Alice", chat_type="group", chat_name="Test Chat",
            thread_id=None,
        ),
        reply_to_message_id=None,
        reply_to_author_name=None,
        media_urls=media_urls or [],
        media_types=media_types or [],
    )


def _stub_describe(plugin, mapping=None, *, default="A photo of a whiteboard diagram."):
    """Replace _describe_image_sync with a deterministic stub. `mapping` maps
    image_path -> description (or None to simulate a failed vision call)."""
    calls = []

    def _fake(image_path):
        calls.append(image_path)
        if mapping is not None:
            return mapping.get(image_path, default)
        return default

    plugin._describe_image_sync = _fake
    return calls


# ── real dispatch (uid set) always describes ────────────────────────────────

def test_real_dispatch_with_image_gets_description(plugin):
    calls = _stub_describe(plugin)
    msg = _message(
        "[image 'photo.jpg' saved at: /tmp/photo.jpg]",
        from_user_id="222",
        media_urls=["/tmp/photo.jpg"], media_types=["image/jpeg"],
    )

    plugin._ingest(msg)

    assert calls == ["/tmp/photo.jpg"]
    assert "[image description: A photo of a whiteboard diagram.]" in msg.text
    stored = plugin.store.recent("-100700", 10)["messages"]
    assert len(stored) == 1
    assert "[image description:" in stored[0]["text"]


# ── observed (uid empty), chat NOT active -> no description call ───────────

def test_observed_message_in_idle_chat_is_not_described(plugin):
    calls = _stub_describe(plugin)
    msg = _message(
        "[image 'photo.jpg' saved at: /tmp/photo.jpg]",
        from_user_id="",
        media_urls=["/tmp/photo.jpg"], media_types=["image/jpeg"],
    )

    plugin._ingest(msg)

    assert calls == []
    assert "[image description:" not in msg.text
    assert "[image 'photo.jpg' saved at: /tmp/photo.jpg]" in msg.text


# ── observed (uid empty), chat IS active (recent prior message) -> described

def test_observed_message_in_active_chat_is_described(plugin):
    # Seed a message a few seconds ago so the chat counts as "active".
    plugin.store.add({
        "chat_id": "-100700", "message_id": "0", "ts": time.time() - 5,
        "user_id": "333", "user_name": "Bob", "chat_type": "group",
        "chat_name": "Test Chat", "thread_id": "", "text": "hey",
        "reply_to_message_id": "", "reply_to_author": "",
    })
    calls = _stub_describe(plugin)
    msg = _message(
        "[image 'photo.jpg' saved at: /tmp/photo.jpg]",
        from_user_id="", message_id="1",
        media_urls=["/tmp/photo.jpg"], media_types=["image/jpeg"],
    )

    plugin._ingest(msg)

    assert calls == ["/tmp/photo.jpg"]
    assert "[image description:" in msg.text


# ── toggle disabled -> never describes, even on real dispatch ──────────────

def test_toggle_disabled_skips_description(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_AUTO_DESCRIBE_IMAGES", "0")
    calls = _stub_describe(plugin)
    msg = _message(
        "[image 'photo.jpg' saved at: /tmp/photo.jpg]",
        from_user_id="222",
        media_urls=["/tmp/photo.jpg"], media_types=["image/jpeg"],
    )

    plugin._ingest(msg)

    assert calls == []
    assert "[image description:" not in msg.text


# ── multiple images -> one description note per image, in order ───────────

def test_multiple_images_each_get_a_description(plugin):
    calls = _stub_describe(plugin, mapping={
        "/tmp/a.jpg": "First image: a circuit board.",
        "/tmp/b.jpg": "Second image: a wiring diagram.",
    })
    msg = _message(
        "caption",
        from_user_id="222",
        media_urls=["/tmp/a.jpg", "/tmp/b.jpg"],
        media_types=["image/jpeg", "image/png"],
    )

    plugin._ingest(msg)

    assert calls == ["/tmp/a.jpg", "/tmp/b.jpg"]
    assert "[image description: First image: a circuit board.]" in msg.text
    assert "[image description: Second image: a wiring diagram.]" in msg.text


# ── non-image media types are left alone ────────────────────────────────────

def test_non_image_media_is_not_described(plugin):
    calls = _stub_describe(plugin)
    msg = _message(
        "[document 'report.pdf' saved at: /tmp/report.pdf]",
        from_user_id="222",
        media_urls=["/tmp/report.pdf"], media_types=["application/pdf"],
    )

    plugin._ingest(msg)

    assert calls == []
    assert "[image description:" not in msg.text


# ── a failed/None description falls back to the plain existing note ────────

def test_failed_description_leaves_text_unchanged(plugin):
    _stub_describe(plugin, mapping={"/tmp/photo.jpg": None})
    original_text = "[image 'photo.jpg' saved at: /tmp/photo.jpg]"
    msg = _message(
        original_text,
        from_user_id="222",
        media_urls=["/tmp/photo.jpg"], media_types=["image/jpeg"],
    )

    plugin._ingest(msg)

    assert msg.text == original_text


# ── no media at all -> _describe_image_sync never called ───────────────────

def test_text_only_message_skips_vision_entirely(plugin):
    calls = _stub_describe(plugin)
    msg = _message("just text", from_user_id="222")

    plugin._ingest(msg)

    assert calls == []
