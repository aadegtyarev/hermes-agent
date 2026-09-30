#!/usr/bin/env python3
"""Nightly backup of telegram.db (this plugin's message/allowlist store).

Registered as a no_agent hermes cron job by telegram-context's register()
(see _ensure_backup_cron_job in __init__.py) — this file is copied to
$HERMES_HOME/scripts/ at plugin load time, which is where hermes's cron
scheduler requires script= paths to resolve.

Uses sqlite3.Connection.backup() — SQLite's own Online Backup API — instead
of copying the .db file directly. telegram.db runs in WAL mode (concurrent
readers/writer); a plain file copy taken mid-write can land between the main
file and its -wal journal and produce a torn, inconsistent backup that looks
fine until you actually try to restore it. The backup API takes a proper
page-level snapshot regardless of what's concurrently writing to the source.

Silent on success (no_agent jobs only deliver non-empty stdout — see
cron/jobs.py's create_job docstring), so this produces no chat noise on a
normal night. A failure prints to stdout, which the scheduler does deliver.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone

from hermes_constants import get_hermes_home

KEEP = 2


def main() -> int:
    try:
        src_path = get_hermes_home() / "telegram.db"
        if not src_path.exists():
            return 0  # nothing ingested yet — not an error, nothing to back up

        backup_dir = get_hermes_home() / "telegram_db_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest_path = backup_dir / f"telegram-{stamp}.db"

        src = sqlite3.connect(str(src_path))
        try:
            dest = sqlite3.connect(str(dest_path))
            try:
                src.backup(dest)
            finally:
                dest.close()
        finally:
            src.close()

        # Rotate: keep only the KEEP most recent backups.
        existing = sorted(
            backup_dir.glob("telegram-*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in existing[KEEP:]:
            stale.unlink(missing_ok=True)

        return 0
    except Exception as e:  # noqa: BLE001
        print(f"telegram.db backup FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
