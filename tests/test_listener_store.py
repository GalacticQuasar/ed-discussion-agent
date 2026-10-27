"""listener.store: the SQLite database (a temporary file per test, see conftest)."""

from contextlib import closing

import pytest

from ed_discussion_mcp.listener import store


@pytest.fixture
def conn():
    with closing(store.connect()) as conn:
        yield conn


def test_db_path_defaults_to_the_user_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("ED_LISTENER_DB")
    monkeypatch.setattr(store.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    assert store.db_path() == tmp_path / "ed-discussion-mcp" / "listener.db"


def test_connect_creates_missing_directories(monkeypatch, tmp_path):
    monkeypatch.setenv("ED_LISTENER_DB", str(tmp_path / "a" / "b" / "listener.db"))

    with closing(store.connect()):
        pass

    assert (tmp_path / "a" / "b" / "listener.db").exists()


def test_add_list_and_delete_rules(conn):
    rule = store.add_rule(conn, "tell me about extensions", "Was a deadline moved?", [5, 3, 5], True, "once")

    assert rule["id"] == 1
    assert rule["course_ids"] == [3, 5]
    assert rule["staff_only"] is True
    assert rule["enabled"] is True
    assert rule["threshold"] is None
    assert store.list_rules(conn) == [rule]

    assert store.delete_rule(conn, 1) is True
    assert store.delete_rule(conn, 1) is False
    assert store.list_rules(conn) == []


def test_rules_are_visible_to_another_connection(conn):
    store.add_rule(conn, "r", "q", [1], False, "always", 0.9)

    with closing(store.connect()) as other:
        [rule] = store.list_rules(other)

    assert rule["threshold"] == 0.9


def test_mode_must_be_once_or_always(conn):
    with pytest.raises(Exception, match="CHECK"):
        store.add_rule(conn, "r", "q", [1], False, "sometimes")


def test_ntfy_topic_is_generated_once(conn):
    topic = store.ntfy_topic(conn)

    assert topic.startswith("ed-") and len(topic) > 20
    with closing(store.connect()) as other:
        assert store.ntfy_topic(other) == topic


def test_ntfy_topic_from_env_wins(conn, monkeypatch):
    store.ntfy_topic(conn)
    monkeypatch.setenv("ED_LISTENER_NTFY_TOPIC", "mine")

    assert store.ntfy_topic(conn) == "mine"


def test_heartbeat_round_trips(conn):
    assert store.last_heartbeat(conn) is None

    store.heartbeat(conn, "0.2.0")

    assert store.last_heartbeat(conn)["version"] == "0.2.0"


def test_record_match_claims_once_and_unsent_skips_fresh_pending(conn):
    rule = store.add_rule(conn, "r", "q", [1], False, "once")

    assert store.record_match(conn, "thread:1", [rule], {"title": "t"}) is True
    assert store.record_match(conn, "thread:1", [rule], {"title": "t"}) is False
    assert store.get_rule(conn, rule["id"])["enabled"] is False
    assert store.unsent(conn, "2000-01-01") == []
    assert store.unsent(conn, "9999") == [("thread:1", {"title": "t"})]

    store.mark_sent(conn, "thread:1", ok=False)
    assert store.unsent(conn, "2000-01-01") == [("thread:1", {"title": "t"})]


def test_advance_cursor_never_moves_back(conn):
    store.advance_cursor(conn, 1, "2026-10-12T00:00:00+00:00")
    store.advance_cursor(conn, 1, "2026-10-11T00:00:00+00:00")

    assert store.get_cursor(conn, 1) == "2026-10-12T00:00:00+00:00"
