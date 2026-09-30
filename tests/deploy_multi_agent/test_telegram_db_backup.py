"""Tests for deploy/multi-agent/base/plugins/telegram-context/telegram_db_backup.py.

Uses sqlite3.Connection.backup() (SQLite's Online Backup API) rather than
copying the .db file directly — telegram.db runs in WAL mode, and a plain
file copy taken mid-write can land between the main file and its -wal
journal, producing a torn backup that looks fine until you try to restore
it. Registered as a nightly no_agent cron job by telegram-context's
_ensure_backup_cron_job() (see test_telegram_context_plugin.py).
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "deploy" / "multi-agent" / "base" / "plugins" / "telegram-context"
    / "telegram_db_backup.py"
)


@pytest.fixture
def backup_mod(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("telegram_db_backup_under_test", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_db(hermes_home: Path, text="hello"):
    db_path = hermes_home / "telegram.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, text TEXT)")
    conn.execute("INSERT INTO messages(text) VALUES (?)", (text,))
    conn.commit()
    conn.close()
    return db_path


def test_no_source_db_is_a_silent_no_op(backup_mod):
    """Nothing ingested yet — not an error, nothing to back up."""
    assert backup_mod.main() == 0


def test_backup_produces_a_valid_readable_copy(backup_mod, tmp_path):
    _seed_db(tmp_path, text="hello world")

    rc = backup_mod.main()

    assert rc == 0
    backups = list((tmp_path / "telegram_db_backups").glob("telegram-*.db"))
    assert len(backups) == 1
    conn = sqlite3.connect(str(backups[0]))
    rows = conn.execute("SELECT text FROM messages").fetchall()
    assert rows == [("hello world",)]


def test_rotation_keeps_only_the_two_most_recent(backup_mod, tmp_path):
    _seed_db(tmp_path)

    for _ in range(4):
        assert backup_mod.main() == 0
        time.sleep(1.05)  # filenames have 1s resolution; force distinct names

    backups = sorted((tmp_path / "telegram_db_backups").glob("telegram-*.db"))
    assert len(backups) == 2


def test_rotation_keeps_the_newest_ones_by_mtime(backup_mod, tmp_path):
    _seed_db(tmp_path)

    names = []
    for _ in range(3):
        backup_mod.main()
        names.append(sorted((tmp_path / "telegram_db_backups").glob("telegram-*.db"))[-1].name)
        time.sleep(1.05)

    remaining = {p.name for p in (tmp_path / "telegram_db_backups").glob("telegram-*.db")}
    assert remaining == set(names[-2:])


def test_backup_survives_a_concurrent_wal_writer(backup_mod, tmp_path):
    """The whole point of using sqlite3's backup API instead of copying the
    file: it must produce a consistent snapshot even with another connection
    actively holding the database open in WAL mode."""
    db_path = _seed_db(tmp_path)
    writer = sqlite3.connect(str(db_path))
    writer.execute("PRAGMA journal_mode=WAL")

    try:
        rc = backup_mod.main()
        assert rc == 0
        backups = list((tmp_path / "telegram_db_backups").glob("telegram-*.db"))
        assert len(backups) == 1
        conn = sqlite3.connect(str(backups[0]))
        assert conn.execute("SELECT text FROM messages").fetchall() == [("hello",)]
    finally:
        writer.close()
