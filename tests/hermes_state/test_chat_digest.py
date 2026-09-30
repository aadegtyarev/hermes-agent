import time

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    database = SessionDB(tmp_path / "state.db")
    try:
        yield database
    finally:
        database.close()


def _seed_chat(db: SessionDB, session_id="grp1", chat_id="chat-1", n=20, source="telegram"):
    db.create_session(session_id, source=source, chat_id=chat_id)
    base = time.time() - n * 60
    ids = []
    for i in range(n):
        observed = i % 3 != 0  # every 3rd message is a "triggered" (non-observed) turn
        content = f"message {i} about topic-A" if i % 5 == 0 else f"message {i} filler"
        mid = db.append_message(
            session_id, role="user", content=content,
            observed=observed, timestamp=base + i * 60,
        )
        ids.append(mid)
    return ids


def test_no_matching_chat_returns_empty(db):
    result = db.get_chat_digest_page(source="telegram", chat_id="nonexistent")
    assert result["matched_sessions"] == 0
    assert result["returned"] == 0
    assert result["messages"] == []
    assert result["has_more"] is False
    assert result["next_cursor"] is None


def test_count_only_matches_sum_of_paginated_batches(db):
    _seed_chat(db, n=37)

    count = db.get_chat_digest_page(source="telegram", chat_id="chat-1", count_only=True)
    assert count["matched_sessions"] == 1
    assert count["total_count"] == 37

    seen_ids = []
    cursor = 0
    while True:
        page = db.get_chat_digest_page(
            source="telegram", chat_id="chat-1", after_id=cursor, limit=10,
        )
        seen_ids.extend(m["id"] for m in page["messages"])
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        cursor = page["next_cursor"]

    # Every matching row visited exactly once, no skips or duplicates.
    assert len(seen_ids) == len(set(seen_ids)) == count["total_count"]


def test_pagination_is_chronological_and_gapless(db):
    ids = _seed_chat(db, n=25)

    all_msgs = []
    cursor = 0
    while True:
        page = db.get_chat_digest_page(
            source="telegram", chat_id="chat-1", after_id=cursor, limit=7,
        )
        all_msgs.extend(page["messages"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]

    assert [m["id"] for m in all_msgs] == ids
    assert [m["timestamp"] for m in all_msgs] == sorted(m["timestamp"] for m in all_msgs)


def test_time_window_bounds(db):
    _seed_chat(db, n=10)
    count_all = db.get_chat_digest_page(source="telegram", chat_id="chat-1", count_only=True)

    now = time.time()
    narrow = db.get_chat_digest_page(
        source="telegram", chat_id="chat-1", since_ts=now - 3 * 60, until_ts=now,
        count_only=True,
    )
    assert 0 < narrow["total_count"] < count_all["total_count"]


def test_scoping_by_source_and_chat_id_and_thread_id(db):
    db.create_session("a", source="telegram", chat_id="1", thread_id="10")
    db.create_session("b", source="telegram", chat_id="1", thread_id="20")
    db.create_session("c", source="telegram", chat_id="2")
    db.create_session("d", source="discord", chat_id="1")
    for sid in ("a", "b", "c", "d"):
        db.append_message(sid, role="user", content=f"hi from {sid}")

    only_thread_10 = db.get_chat_digest_page(
        source="telegram", chat_id="1", thread_id="10", count_only=True,
    )
    assert only_thread_10["total_count"] == 1
    assert only_thread_10["matched_sessions"] == 1

    both_threads = db.get_chat_digest_page(source="telegram", chat_id="1", count_only=True)
    assert both_threads["matched_sessions"] == 2
    assert both_threads["total_count"] == 2

    other_chat = db.get_chat_digest_page(source="telegram", chat_id="2", count_only=True)
    assert other_chat["total_count"] == 1

    other_source = db.get_chat_digest_page(source="discord", chat_id="1", count_only=True)
    assert other_source["total_count"] == 1


def test_compacted_included_rewound_excluded(db):
    db.create_session("s1", source="telegram", chat_id="chat-1")
    live_id = db.append_message("s1", role="user", content="live message")
    compacted_id = db.append_message("s1", role="user", content="compacted message")
    rewound_id = db.append_message("s1", role="user", content="rewound message")

    db._conn.execute("UPDATE messages SET active=0, compacted=1 WHERE id = ?", (compacted_id,))
    db._conn.execute("UPDATE messages SET active=0, compacted=0 WHERE id = ?", (rewound_id,))
    db._conn.commit()

    page = db.get_chat_digest_page(source="telegram", chat_id="chat-1", limit=100)
    returned_ids = {m["id"] for m in page["messages"]}
    assert live_id in returned_ids
    assert compacted_id in returned_ids
    assert rewound_id not in returned_ids


def test_observed_flag_preserved(db):
    _seed_chat(db, n=9)
    page = db.get_chat_digest_page(source="telegram", chat_id="chat-1", limit=100)
    observed_flags = {m["id"]: m["observed"] for m in page["messages"]}
    assert True in observed_flags.values()
    assert False in observed_flags.values()


def test_query_narrows_by_substring(db):
    _seed_chat(db, n=20)
    unfiltered = db.get_chat_digest_page(source="telegram", chat_id="chat-1", count_only=True)
    narrowed = db.get_chat_digest_page(
        source="telegram", chat_id="chat-1", query="topic-A", count_only=True,
    )
    assert 0 < narrowed["total_count"] < unfiltered["total_count"]

    page = db.get_chat_digest_page(source="telegram", chat_id="chat-1", query="topic-A", limit=100)
    assert all("topic-A" in m["content"] for m in page["messages"])


def test_roles_filter(db):
    db.create_session("s1", source="telegram", chat_id="chat-1")
    db.append_message("s1", role="user", content="user says hi")
    db.append_message("s1", role="assistant", content="bot replies")

    user_only = db.get_chat_digest_page(source="telegram", chat_id="chat-1", roles=["user"], limit=100)
    assert {m["role"] for m in user_only["messages"]} == {"user"}

    both = db.get_chat_digest_page(
        source="telegram", chat_id="chat-1", roles=["user", "assistant"], limit=100,
    )
    assert {m["role"] for m in both["messages"]} == {"user", "assistant"}
