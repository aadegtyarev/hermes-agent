import json
import sys
import time

import pytest

import hermes_state
from hermes_state import SessionDB


@pytest.fixture(autouse=True)
def _isolated_default_db(tmp_path, monkeypatch):
    # hermes_state.DEFAULT_DB_PATH (the path SessionDB() falls back to with no
    # explicit db_path — exactly what `cmd_sessions`'s `db = SessionDB()` uses)
    # is a module-level constant evaluated at import time, i.e. during pytest
    # collection — *before* the per-test HERMES_HOME-isolating fixture in
    # conftest.py ever runs. Left alone, every `SessionDB()` call in this file
    # (both ours and the CLI's) would resolve to the real ~/.hermes/state.db.
    # Pin it to a per-test tempfile explicitly so this file's CLI-level tests
    # never touch the real database.
    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", tmp_path / "state.db")


def _seed(chat_id, n=25):
    db = SessionDB()
    try:
        db.create_session(f"grp-{chat_id}", source="telegram", chat_id=chat_id)
        base = time.time() - n * 60
        for i in range(n):
            db.append_message(
                f"grp-{chat_id}", role="user", content=f"message {i}",
                observed=(i % 2 == 0), timestamp=base + i * 60,
            )
    finally:
        db.close()


def test_count_only_reports_totals(monkeypatch, capsys):
    import hermes_cli.main as main_mod

    _seed("chat-count-only", n=12)
    monkeypatch.setattr(
        sys, "argv",
        ["hermes", "sessions", "digest", "--chat-id", "chat-count-only",
         "--source", "telegram", "--count-only"],
    )

    main_mod.main()

    out = capsys.readouterr().out
    result = json.loads(out)
    assert result["matched_sessions"] == 1
    assert result["total_count"] == 12


def test_paging_reconstructs_full_message_set(monkeypatch, capsys):
    import hermes_cli.main as main_mod

    _seed("chat-paging", n=23)

    seen_ids = []
    cursor = 0
    while True:
        monkeypatch.setattr(
            sys, "argv",
            ["hermes", "sessions", "digest", "--chat-id", "chat-paging",
             "--source", "telegram", "--format", "jsonl",
             "--limit", "9", "--cursor", str(cursor)],
        )
        main_mod.main()
        out = capsys.readouterr().out
        lines = [json.loads(line) for line in out.strip().splitlines()]
        meta = next(line["_meta"] for line in lines if "_meta" in line)
        batch_ids = [line["id"] for line in lines if "_meta" not in line]
        seen_ids.extend(batch_ids)
        if not meta["has_more"]:
            assert meta["next_cursor"] is None
            break
        cursor = meta["next_cursor"]

    assert len(seen_ids) == len(set(seen_ids)) == 23


def test_text_format_includes_observed_tag_and_meta_footer(monkeypatch, capsys):
    import hermes_cli.main as main_mod

    _seed("chat-text-format", n=3)
    monkeypatch.setattr(
        sys, "argv",
        ["hermes", "sessions", "digest", "--chat-id", "chat-text-format", "--source", "telegram"],
    )

    main_mod.main()

    out = capsys.readouterr().out
    assert "(observed)" in out
    assert "=== META " in out
    meta_line = next(line for line in out.splitlines() if line.startswith("=== META "))
    meta = json.loads(meta_line[len("=== META "):-len(" ===")])
    assert meta["returned"] == 3
    assert meta["has_more"] is False


def test_no_match_for_unknown_chat(monkeypatch, capsys):
    import hermes_cli.main as main_mod

    monkeypatch.setattr(
        sys, "argv",
        ["hermes", "sessions", "digest", "--chat-id", "does-not-exist",
         "--source", "telegram", "--count-only"],
    )

    main_mod.main()

    out = capsys.readouterr().out
    result = json.loads(out)
    assert result["matched_sessions"] == 0
    assert result["total_count"] == 0
