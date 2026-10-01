"""Tests for deploy/multi-agent/base/plugins/telegram-context.

Focus: the pre_tool_call hard read-only guard (_pre_tool_call) and the
regression it was written to fix — that /hermes_readonly must never lock an
admin out of managing the very chat it was run in. See the module docstring
in telegram-context/__init__.py for the full story (an earlier version tried
to get the "never writes there" guarantee by mirroring this plugin's readonly
set into hermes-agent core's own read_only_chats field, which also gates
dispatch and silently stopped /hermes_forget from reaching this plugin's own
hook for a chat it had just marked read-only).

This deploy bundle isn't part of the importable hermes-agent package tree
(hyphenated directory name, loaded by render.py's own file-copy bundling at
deploy time, not a Python package) — loaded here via importlib the same way,
so these tests exercise the real module rather than a reimplementation.
"""
from __future__ import annotations

import importlib.util
import itertools
import sys
import types
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
    """Load a fresh copy of the plugin module, with its own isolated
    HERMES_HOME (so store.py's SQLite file never touches a real one) and a
    stubbed `tools` submodule (tools.py pulls in tools.registry, core
    plumbing unrelated to what's under test here).

    A unique module name per call keeps tests independent even though
    they share a subprocess (run_tests_parallel.py isolates per FILE, not
    per test function).
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_READONLY_CHATS", raising=False)
    monkeypatch.delenv("TELEGRAM_HOME_CHANNEL", raising=False)

    pkg_name = f"telegram_context_under_test_{next(_counter)}"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(_PLUGIN_DIR)]
    sys.modules[pkg_name] = pkg

    store_spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.store", _PLUGIN_DIR / "store.py"
    )
    store_mod = importlib.util.module_from_spec(store_spec)
    sys.modules[f"{pkg_name}.store"] = store_mod
    store_spec.loader.exec_module(store_mod)

    tools_mod = types.ModuleType(f"{pkg_name}.tools")
    tools_mod.TOOLS = ()
    tools_mod.BATCH_REVIEW_TOOLS = ()
    sys.modules[f"{pkg_name}.tools"] = tools_mod

    init_spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.__init__", _PLUGIN_DIR / "__init__.py"
    )
    mod = importlib.util.module_from_spec(init_spec)
    mod.__package__ = pkg_name
    sys.modules[f"{pkg_name}.__init__"] = mod
    init_spec.loader.exec_module(mod)
    mod.store.init()

    yield mod

    for key in list(sys.modules):
        if key.startswith(pkg_name):
            del sys.modules[key]


def _group_message(text, *, chat_id="-100", from_user_id="111", chat_name="Test Group"):
    return SimpleNamespace(
        message_id="42",
        text=text,
        source=SimpleNamespace(
            platform="telegram", chat_id=chat_id, user_id=from_user_id,
            user_name="Alice", chat_type="group", chat_name=chat_name,
            thread_id=None,
        ),
        reply_to_message_id=None,
        reply_to_author_name=None,
    )


# ── _pre_tool_call: the actual hard guarantee ───────────────────────────────

def test_blocks_send_message_targeting_a_readonly_chat_env(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_READONLY_CHATS", "-100")

    result = plugin._pre_tool_call(
        tool_name="send_message", args={"target": "telegram:-100", "message": "hi"},
    )

    assert result is not None
    assert result["action"] == "block"
    assert "-100" in result["message"]


def test_blocks_send_message_targeting_a_readonly_chat_from_runtime_store(plugin):
    plugin.store.set_chat("-200", "readonly", "Public chat", "662750197")

    result = plugin._pre_tool_call(
        tool_name="send_message", args={"target": "telegram:-200", "message": "hi"},
    )

    assert result is not None and result["action"] == "block"


def test_allows_send_message_to_a_non_readonly_chat(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_READONLY_CHATS", "-100")

    result = plugin._pre_tool_call(
        tool_name="send_message", args={"target": "telegram:-999", "message": "hi"},
    )

    assert result is None


def test_thread_suffix_does_not_bypass_the_block(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_READONLY_CHATS", "-100")

    result = plugin._pre_tool_call(
        tool_name="send_message",
        args={"target": "telegram:-100:17585", "message": "hi"},
    )

    assert result is not None and result["action"] == "block"


def test_bare_telegram_target_resolves_against_home_channel(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_READONLY_CHATS", "-100")
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "-100")

    result = plugin._pre_tool_call(
        tool_name="send_message", args={"target": "telegram", "message": "hi"},
    )

    assert result is not None and result["action"] == "block"


def test_react_action_is_blocked_too_not_just_plain_send(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_READONLY_CHATS", "-100")

    result = plugin._pre_tool_call(
        tool_name="send_message",
        args={"action": "react", "target": "telegram:-100", "emoji": "\U0001f44d"},
    )

    assert result is not None and result["action"] == "block"


def test_other_platforms_never_matched_even_with_colliding_numeric_id(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_READONLY_CHATS", "999888777")

    result = plugin._pre_tool_call(
        tool_name="send_message",
        args={"target": "discord:999888777:555444333", "message": "hi"},
    )

    assert result is None


def test_ignores_tool_calls_other_than_send_message(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_READONLY_CHATS", "-100")

    result = plugin._pre_tool_call(
        tool_name="terminal", args={"command": "echo telegram:-100"},
    )

    assert result is None


def test_missing_or_empty_target_is_a_safe_no_op(plugin):
    assert plugin._pre_tool_call(tool_name="send_message", args={}) is None
    assert plugin._pre_tool_call(tool_name="send_message", args=None) is None


# ── Regression: /hermes_forget must still work from inside the RO chat ─────
# (the actual bug in the reverted core-mirroring approach — a chat marked
# read-only silently stopped reaching this plugin's own dispatch hook at all)

def test_hermes_readonly_then_hermes_forget_round_trip_from_inside_the_chat(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    chat_id = "-300"

    msg1 = _group_message("/hermes_readonly", chat_id=chat_id)
    result1 = plugin._handle_command(msg1, msg1.source, chat_id, "111")
    assert result1 == {"action": "skip", "reason": "telegram chat command handled"}
    assert chat_id in plugin._readonly_chats()

    # The whole point: a second admin command typed INSIDE the now-read-only
    # chat must still reach _handle_command — nothing in this plugin's own
    # logic gates dispatch on chats_allowed before admin commands are checked.
    msg2 = _group_message("/hermes_forget", chat_id=chat_id)
    result2 = plugin._handle_command(msg2, msg2.source, chat_id, "111")
    assert result2 == {"action": "skip", "reason": "telegram chat command handled"}
    assert chat_id not in plugin._readonly_chats()


def test_non_admin_cannot_run_hermes_commands(plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    msg = _group_message("/hermes_readonly", chat_id="-400")

    result = plugin._handle_command(msg, msg.source, "-400", "999")

    assert result == {"action": "skip", "reason": "telegram chat command from non-admin"}
    assert "-400" not in plugin._readonly_chats()


def test_non_admin_gets_total_silence_not_a_permission_denied_reply(plugin, monkeypatch):
    """A non-admin poking /hermes_* must get no reply at all — not even an
    error — so the command's existence isn't confirmed to them."""
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "111")
    sent = []
    monkeypatch.setattr(plugin, "_send", lambda chat_id, text: sent.append((chat_id, text)))
    msg = _group_message("/hermes_here", chat_id="-400")

    plugin._handle_command(msg, msg.source, "-400", "999")

    assert sent == []


# ── Regression: the removed core-mirroring function is actually gone ───────

def test_sync_core_read_only_was_removed(plugin):
    """Guards against silently reintroducing the reverted approach (see the
    module docstring): mirroring into hermes-agent core's read_only_chats
    also gates core dispatch, which is what caused the /hermes_forget
    lockout this test file exists to prevent."""
    assert not hasattr(plugin, "_sync_core_read_only")


def test_register_installs_the_pre_tool_call_hook(plugin, monkeypatch):
    hook_calls = []
    tool_calls = []

    class FakeCtx:
        def register_tool(self, **kw):
            tool_calls.append(kw)

        def register_hook(self, name, handler):
            hook_calls.append((name, handler))

        def register_command(self, **kw):
            pass

    # This fixture stubs tools.py to an empty TOOLS/BATCH_REVIEW_TOOLS — swap
    # in one fake entry for BATCH_REVIEW_TOOLS just for this test, so the
    # toolset-wiring logic in register() itself is actually exercised here.
    monkeypatch.setattr(plugin.T, "BATCH_REVIEW_TOOLS",
                         (("partner_flag_chats", {"name": "partner_flag_chats"}, lambda a, **k: "{}", "🚩"),))

    plugin.register(FakeCtx())

    hook_names = [name for name, _ in hook_calls]
    assert "pre_tool_call" in hook_names
    assert "pre_gateway_dispatch" in hook_names
    registered = dict(hook_calls)
    assert registered["pre_tool_call"] is plugin._pre_tool_call

    batch_tool_calls = [c for c in tool_calls if c.get("name") == "partner_flag_chats"]
    assert len(batch_tool_calls) == 1
    assert batch_tool_calls[0]["toolset"] == plugin._BATCH_REVIEW_TOOLSET


# ── Nightly backup cron job registration ────────────────────────────────────

def test_ensure_backup_cron_job_registers_once(plugin, tmp_path):
    pytest.importorskip("croniter")  # real hermes-agent dependency; may be
    # missing only in an ad-hoc local venv, never in the production image
    from cron import jobs as cron_jobs

    plugin._ensure_backup_cron_job()

    registered = [j for j in cron_jobs.list_jobs(include_disabled=True)
                  if j.get("name") == plugin._BACKUP_JOB_NAME]
    assert len(registered) == 1
    job = registered[0]
    assert job.get("script") == plugin._BACKUP_SCRIPT_FILENAME
    assert job.get("no_agent") is True

    script_copy = tmp_path / "scripts" / plugin._BACKUP_SCRIPT_FILENAME
    assert script_copy.exists()
    assert "sqlite3" in script_copy.read_text(encoding="utf-8")

    # Idempotent: calling again must not create a second job.
    plugin._ensure_backup_cron_job()
    still_one = [j for j in cron_jobs.list_jobs(include_disabled=True)
                 if j.get("name") == plugin._BACKUP_JOB_NAME]
    assert len(still_one) == 1


def test_ensure_batch_review_cron_job_registers_once_scoped_to_its_own_toolset(plugin, tmp_path):
    pytest.importorskip("croniter")
    from cron import jobs as cron_jobs

    plugin._ensure_batch_review_cron_job()

    registered = [j for j in cron_jobs.list_jobs(include_disabled=True)
                  if j.get("name") == plugin._BATCH_REVIEW_JOB_NAME]
    assert len(registered) == 1
    job = registered[0]
    assert job.get("script") == plugin._BATCH_REVIEW_SCRIPT_FILENAME
    # Agent-invoking (unlike the backup job) but scoped to ONLY its own
    # toolset — the whole point is this job's agent cannot do anything but
    # call partner_flag_chats on what the script handed it.
    assert not job.get("no_agent")
    assert job.get("enabled_toolsets") == [plugin._BATCH_REVIEW_TOOLSET]

    script_copy = tmp_path / "scripts" / plugin._BATCH_REVIEW_SCRIPT_FILENAME
    assert script_copy.exists()
    assert "_due_chats" in script_copy.read_text(encoding="utf-8")

    plugin._ensure_batch_review_cron_job()
    still_one = [j for j in cron_jobs.list_jobs(include_disabled=True)
                 if j.get("name") == plugin._BATCH_REVIEW_JOB_NAME]
    assert len(still_one) == 1


def test_dedupe_cron_jobs_keeps_only_the_oldest(plugin):
    """Regression guard: a real production incident — two near-simultaneous
    register() calls both saw "not yet registered" before either create()
    call had persisted, producing two jobs with the same name. Belt-and-
    suspenders cleanup so a race this plugin load hit self-heals on the
    next one, regardless of exactly how the race happened."""
    pytest.importorskip("croniter")
    from cron import jobs as cron_jobs

    older = cron_jobs.create_job(prompt="test job", schedule="0 3 * * *", name="dupe-test")
    newer = cron_jobs.create_job(prompt="test job", schedule="0 3 * * *", name="dupe-test")
    assert older["id"] != newer["id"]

    plugin._dedupe_cron_jobs_by_name(cron_jobs, "dupe-test")

    remaining = [j for j in cron_jobs.list_jobs(include_disabled=True) if j.get("name") == "dupe-test"]
    assert len(remaining) == 1
    assert remaining[0]["id"] == older["id"]


# ── Client-chat isolation: telegram_search/telegram_recent/session_search/
# send_message must all be pinned to (or blocked outside of) a client chat's
# own history — the one-directional guarantee described in _pre_tool_call's
# docstring. _origin_chat_id resolves session_id -> chat_id via hermes_state's
# SessionDB, so these tests fake that class rather than standing up a real one.

class _FakeSessionDB:
    """Maps session_id -> chat_id for origin_chat_id, set per test via _SESSIONS."""
    _SESSIONS: dict[str, str] = {}

    def __init__(self, read_only: bool = False):
        pass

    def get_session(self, session_id):
        chat_id = self._SESSIONS.get(session_id)
        return {"chat_id": chat_id, "source": "telegram", "parent_session_id": None} if chat_id is not None else None

    def close(self):
        pass


@pytest.fixture
def fake_session_db(monkeypatch):
    import hermes_state

    _FakeSessionDB._SESSIONS = {}
    monkeypatch.setattr(hermes_state, "SessionDB", _FakeSessionDB)
    return _FakeSessionDB


def _client_session(plugin, fake_session_db, session_id: str, chat_id: str, program: str = "journalist-partners"):
    plugin.store.create_program(program, "-500", "111")
    plugin.store.set_chat(chat_id, "client", "Acme", "111", program=program)
    fake_session_db._SESSIONS[session_id] = chat_id


def test_telegram_search_blocked_without_own_chat_id_from_client_chat(plugin, fake_session_db):
    _client_session(plugin, fake_session_db, "sess-1", "-700")

    result = plugin._pre_tool_call(
        tool_name="telegram_search", args={"query": "hello"}, session_id="sess-1",
    )

    assert result is not None and result["action"] == "block"


def test_telegram_search_blocked_for_a_different_chat_id_from_client_chat(plugin, fake_session_db):
    _client_session(plugin, fake_session_db, "sess-1", "-700")

    result = plugin._pre_tool_call(
        tool_name="telegram_search", args={"query": "hello", "chat_id": "-999"}, session_id="sess-1",
    )

    assert result is not None and result["action"] == "block"


def test_telegram_search_allowed_scoped_to_its_own_chat_id_from_client_chat(plugin, fake_session_db):
    _client_session(plugin, fake_session_db, "sess-1", "-700")

    result = plugin._pre_tool_call(
        tool_name="telegram_search", args={"query": "hello", "chat_id": "-700"}, session_id="sess-1",
    )

    assert result is None


def test_telegram_recent_default_chat_id_blocked_from_client_chat(plugin, fake_session_db):
    """No chat_id at all (telegram_recent's usual 'default to latest active
    chat' behavior) must NOT quietly fall through to another chat's data."""
    _client_session(plugin, fake_session_db, "sess-1", "-700")

    result = plugin._pre_tool_call(tool_name="telegram_recent", args={}, session_id="sess-1")

    assert result is not None and result["action"] == "block"


def test_session_search_fully_blocked_from_client_chat(plugin, fake_session_db):
    _client_session(plugin, fake_session_db, "sess-1", "-700")

    result = plugin._pre_tool_call(
        tool_name="session_search", args={"query": "anything"}, session_id="sess-1",
    )

    assert result is not None and result["action"] == "block"


def test_send_message_blocked_entirely_from_client_chat_even_to_its_own_chat(plugin, fake_session_db):
    """send_message isn't on the client-chat allowlist at all — the only
    legitimate way out is escalate_to_team. Blocking it unconditionally
    (rather than special-casing "but your own chat is fine") means there's
    no separate code path to keep correct, and matches reality in this
    deployment anyway: send_message isn't an agent-callable tool here."""
    _client_session(plugin, fake_session_db, "sess-1", "-700")

    result = plugin._pre_tool_call(
        tool_name="send_message", args={"target": "telegram:-700", "message": "hi"}, session_id="sess-1",
    )

    assert result is not None and result["action"] == "block"


def test_send_message_to_a_different_chat_blocked_from_client_chat(plugin, fake_session_db):
    _client_session(plugin, fake_session_db, "sess-1", "-700")

    result = plugin._pre_tool_call(
        tool_name="send_message", args={"target": "telegram:-999", "message": "hi"}, session_id="sess-1",
    )

    assert result is not None and result["action"] == "block"


def test_send_message_bare_telegram_target_blocked_from_client_chat(plugin, fake_session_db, monkeypatch):
    """Bare 'telegram' resolves against TELEGRAM_HOME_CHANNEL, which is never
    the client chat itself — must not slip through as an implicit allow."""
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "-999")
    _client_session(plugin, fake_session_db, "sess-1", "-700")

    result = plugin._pre_tool_call(
        tool_name="send_message", args={"target": "telegram", "message": "hi"}, session_id="sess-1",
    )

    assert result is not None and result["action"] == "block"


def test_isolation_guards_do_not_apply_to_work_chats(plugin, fake_session_db):
    """Regression guard: the whole point is ONE-DIRECTIONAL isolation — a
    session running in a WORK chat must keep unrestricted cross-chat read
    access (deliberately allowed per the registry's own module docstring)."""
    plugin.store.set_chat("-800", "work", "", "111")
    fake_session_db._SESSIONS["sess-2"] = "-800"

    assert plugin._pre_tool_call(
        tool_name="telegram_search", args={"query": "hello"}, session_id="sess-2",
    ) is None
    assert plugin._pre_tool_call(
        tool_name="session_search", args={"query": "anything"}, session_id="sess-2",
    ) is None
    assert plugin._pre_tool_call(
        tool_name="send_message", args={"target": "telegram:-999"}, session_id="sess-2",
    ) is None


def test_isolation_guards_no_op_without_a_resolvable_session(plugin, fake_session_db):
    """An unresolvable/missing session_id (e.g. a CLI call) must fail OPEN to
    'can't tell', not block — only a session confirmed to be a client chat
    is restricted."""
    assert plugin._pre_tool_call(
        tool_name="telegram_search", args={"query": "hello"}, session_id="unknown-session",
    ) is None
    assert plugin._pre_tool_call(
        tool_name="telegram_search", args={"query": "hello"},
    ) is None  # no session_id kwarg at all


# ── Google Docs isolation: gdoc_read/gdoc_comments pinned to chat_doc_links,
# gdrive_search blocked outright, for client-mode sessions only ────────────

def test_link_chat_docs_extracts_doc_id_from_ingested_text(plugin):
    plugin._link_chat_docs(
        "-700", "please review https://docs.google.com/document/d/DOC123abc/edit?tab=t.0"
    )
    assert plugin.store.chat_doc_ids("-700") == {"DOC123abc"}


def test_link_chat_docs_ignores_text_with_no_gdoc_link(plugin):
    plugin._link_chat_docs("-700", "just a normal message, no links here")
    assert plugin.store.chat_doc_ids("-700") == set()


def test_ingest_populates_chat_doc_links_from_a_real_message(plugin):
    msg = _group_message(
        "here's the draft: https://docs.google.com/document/d/DOC999/edit",
        chat_id="-700",
    )
    plugin._ingest(msg)
    assert plugin.store.chat_doc_ids("-700") == {"DOC999"}


def test_gdoc_read_blocked_for_a_doc_never_shared_in_this_client_chat(plugin, fake_session_db):
    _client_session(plugin, fake_session_db, "sess-1", "-700")

    result = plugin._pre_tool_call(
        tool_name="gdoc_read", args={"url": "https://docs.google.com/document/d/UNSEEN/edit"},
        session_id="sess-1",
    )

    assert result is not None and result["action"] == "block"


def test_gdoc_read_allowed_for_a_doc_the_chat_actually_shared(plugin, fake_session_db):
    _client_session(plugin, fake_session_db, "sess-1", "-700")
    plugin.store.link_chat_doc("-700", "SEEN123")

    result = plugin._pre_tool_call(
        tool_name="gdoc_read", args={"url": "https://docs.google.com/document/d/SEEN123/edit"},
        session_id="sess-1",
    )

    assert result is None


def test_gdoc_comments_same_scoping_as_gdoc_read(plugin, fake_session_db):
    _client_session(plugin, fake_session_db, "sess-1", "-700")
    plugin.store.link_chat_doc("-700", "SEEN123")

    blocked = plugin._pre_tool_call(
        tool_name="gdoc_comments", args={"url": "UNSEEN"}, session_id="sess-1",
    )
    allowed = plugin._pre_tool_call(
        tool_name="gdoc_comments", args={"url": "SEEN123"}, session_id="sess-1",
    )

    assert blocked is not None and blocked["action"] == "block"
    assert allowed is None


def test_gdrive_search_fully_blocked_from_client_chat(plugin, fake_session_db):
    _client_session(plugin, fake_session_db, "sess-1", "-700")

    result = plugin._pre_tool_call(
        tool_name="gdrive_search", args={"query": "budget"}, session_id="sess-1",
    )

    assert result is not None and result["action"] == "block"


def test_gdoc_tools_unaffected_from_work_chats(plugin, fake_session_db):
    """Regression guard: the whole Drive stays reachable from a work chat,
    matching the same one-directional isolation as the other guards."""
    plugin.store.set_chat("-800", "work", "", "111")
    fake_session_db._SESSIONS["sess-2"] = "-800"

    assert plugin._pre_tool_call(
        tool_name="gdrive_search", args={"query": "anything"}, session_id="sess-2",
    ) is None
    assert plugin._pre_tool_call(
        tool_name="gdoc_read", args={"url": "https://docs.google.com/document/d/ANY/edit"},
        session_id="sess-2",
    ) is None


# ── Allowlist regression guards: bypass vectors an adversarial review found
# in the earlier denylist design — each of these must now be blocked by
# DEFAULT (not available, not thought of) rather than requiring a dedicated
# per-tool guard to have been written and kept current.

@pytest.mark.parametrize("tool_name,args", [
    ("telegram_thread", {"message_id": "5", "chat_id": "-999"}),
    ("telegram_dm_allowlist", {}),
    ("session_search", {"query": "anything"}),
    ("gdrive_search", {"query": "anything"}),
    ("gsheet_read", {"url": "https://docs.google.com/spreadsheets/d/ANY/edit"}),
    ("terminal", {"command": "cat /opt/data/telegram.db"}),
    ("code_execution", {"code": "print(1)"}),
    ("file", {"action": "read", "path": "/opt/data/telegram.db"}),
    ("read_file", {"path": "/opt/data/telegram.db"}),
    ("cronjob", {"action": "create", "deliver": "telegram:-800", "prompt": "x", "schedule": "1m"}),
    ("delegate_task", {"goal": "search every telegram chat for X"}),
    ("ssh_connect", {"host": "example.com"}),
    ("memory", {"action": "search", "query": "x"}),
    ("send_message", {"target": "telegram:-700"}),
    ("some_future_tool_nobody_has_written_yet", {}),
])
def test_default_deny_blocks_every_tool_not_on_the_allowlist(plugin, fake_session_db, tool_name, args):
    """Regression guard for the adversarial-review findings: a denylist has
    to enumerate every dangerous tool correctly and stay current forever; an
    allowlist is safe by construction against tools nobody thought to list.
    Each of these was a real, verified bypass of the OLD denylist design."""
    _client_session(plugin, fake_session_db, "sess-1", "-700")

    result = plugin._pre_tool_call(tool_name=tool_name, args=args, session_id="sess-1")

    assert result is not None and result["action"] == "block", f"{tool_name} was NOT blocked"


@pytest.mark.parametrize("tool_name,args", [
    ("clarify", {"question": "which site?"}),
    ("escalate_to_team", {"message": "need help"}),
    ("web_search", {"query": "anything"}),
    ("web_extract", {"url": "https://example.com"}),
    ("vision_analyze", {"image_url": "https://example.com/photo.jpg"}),
])
def test_allowlisted_tools_pass_through_from_client_chat(plugin, fake_session_db, tool_name, args):
    """The flip side of the denylist guard above: the short allowed list
    itself must still actually work (these aren't chat/doc-scoped, so no
    further restriction applies — just confirming they're not accidentally
    swept up by the default-deny)."""
    _client_session(plugin, fake_session_db, "sess-1", "-700")

    result = plugin._pre_tool_call(tool_name=tool_name, args=args, session_id="sess-1")

    assert result is None, f"{tool_name} was unexpectedly blocked: {result}"


def test_allowlist_does_not_apply_to_work_or_readonly_chats(plugin, fake_session_db):
    """The one-directional guarantee must hold for tools newly covered by the
    allowlist too, not just the previously-guarded ones."""
    plugin.store.set_chat("-800", "work", "", "111")
    fake_session_db._SESSIONS["sess-2"] = "-800"

    for tool_name in ("terminal", "cronjob", "delegate_task", "telegram_thread", "gsheet_read"):
        assert plugin._pre_tool_call(tool_name=tool_name, args={}, session_id="sess-2") is None
