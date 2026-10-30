"""listener.tools: preview_rule and registration behind ED_LISTENER.

Ed is faked as in test_server_tools.py, and judge.score is replaced, so no
model or network is involved.
"""

import asyncio
from contextlib import closing

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ed_discussion_mcp.listener import judge, store, tools
from test_server_tools import COURSE, FakeEd, make_thread

STAFF = {"id": 20, "name": "TA", "course_role": "staff"}


@pytest.fixture
def scores(monkeypatch):
    """Score each post by title from this dict (default 0.0), recording what was asked."""
    table = {}
    calls = []

    def score(post, rules, parent=None):
        calls.append((post, rules))
        return {rule_id: table.get(post["title"], 0.0) for rule_id, _ in rules}

    monkeypatch.setattr(judge, "score", score)
    table["calls"] = calls
    return table


def test_preview_scores_recent_threads_highest_first(monkeypatch, scores):
    FakeEd(monkeypatch, [make_thread(1, title="HW3 extended", user=STAFF), make_thread(2, title="Lunch?")])
    scores["HW3 extended"] = 0.95
    scores["Lunch?"] = 0.2

    result = tools.preview_rule(COURSE, "Was a deadline moved?")

    assert result["scored"] == 2
    assert result["would_notify"] == 1
    assert [(t["title"], t["score"], t["would_notify"]) for t in result["threads"]] == [
        ("HW3 extended", 0.95, True),
        ("Lunch?", 0.2, False),
    ]
    post, rules = scores["calls"][0]
    assert rules == [(0, "Was a deadline moved?")]
    assert post == {"title": "HW3 extended", "category": "General", "author_role": "staff", "body": "Body of thread 1"}


def test_preview_staff_only_skips_student_threads(monkeypatch, scores):
    FakeEd(monkeypatch, [make_thread(1), make_thread(2, user=STAFF)])

    result = tools.preview_rule(COURSE, "q", staff_only=True)

    assert [t["title"] for t in result["threads"]] == ["Thread 2"]


def test_preview_skips_private_threads_on_cloud_models(monkeypatch, scores):
    monkeypatch.setenv("ED_LISTENER_MODEL", "clef:cloud")
    FakeEd(monkeypatch, [make_thread(1, is_private=True), make_thread(2)])

    result = tools.preview_rule(COURSE, "q")

    assert [post["title"] for post, _ in scores["calls"]] == ["Thread 2"]
    assert result["skipped_private"] == 1


def test_preview_scores_private_threads_on_cloud_models_when_allowed(monkeypatch, scores):
    monkeypatch.setenv("ED_LISTENER_MODEL", "clef:cloud")
    monkeypatch.setenv("ED_LISTENER_CLOUD_PRIVATE", "1")
    FakeEd(monkeypatch, [make_thread(1, is_private=True)])

    result = tools.preview_rule(COURSE, "q")

    assert result["scored"] == 1
    assert "skipped_private" not in result


def test_preview_respects_limit_and_threshold(monkeypatch, scores):
    FakeEd(monkeypatch, [make_thread(i) for i in range(1, 6)])
    scores["Thread 1"] = 0.5

    result = tools.preview_rule(COURSE, "q", threshold=0.5, limit=3)

    assert result["scored"] == 3
    assert result["would_notify"] == 1


def test_preview_turns_judge_errors_into_tool_errors(monkeypatch):
    FakeEd(monkeypatch, [make_thread(1)])

    def fail(post, rules, parent=None):
        raise judge.JudgeError("Failed to connect to Ollama.")

    monkeypatch.setattr(judge, "score", fail)

    with pytest.raises(ToolError, match="Failed to connect"):
        tools.preview_rule(COURSE, "q")


def test_register_adds_tools_and_instructions():
    mcp = MCPServer("test", instructions="Base.")

    tools.register(mcp)

    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {"preview_rule", "create_rule", "list_rules", "list_notifications", "delete_rule"}
    assert mcp.instructions.startswith("Base.")
    assert "preview_rule" in mcp.instructions


@pytest.fixture
def my_courses(monkeypatch):
    """The user's Ed courses, as get_courses would return them."""
    monkeypatch.setattr(tools.ed_api, "get_courses", lambda token: [{"id": COURSE}, {"id": 2}])
    monkeypatch.setattr(tools.server.ed_api, "get_token", lambda: ("token", False))


def test_create_rule_saves_it_and_reports_status(my_courses):
    result = tools.create_rule([COURSE], " Was a deadline moved? ", "tell me about extensions", mode="once", staff_only=True)

    rule = result["rule"]
    assert rule["instructions"] == "Was a deadline moved?"
    assert rule["course_ids"] == [COURSE]
    assert rule["mode"] == "once"
    assert rule["threshold"] == judge.DEFAULT_THRESHOLD
    assert result["listener_running"] is False
    assert "ed-discussion-mcp listen" in result["hint"]
    assert result["ntfy_subscribe_url"].startswith("https://ntfy.sh/ed-")
    assert tools.list_rules()["rules"] == [rule]


def test_create_rule_rejects_courses_the_user_isnt_in(my_courses):
    with pytest.raises(ToolError, match=r"Not your courses: \[99\]"):
        tools.create_rule([COURSE, 99], "q", "r")


@pytest.mark.parametrize("kwargs, message", [
    ({"course_ids": []}, "at least one course_id"),
    ({"instructions": " "}, "must not be empty"),
    ({"threshold": 1.5}, "between 0 and 1"),
])
def test_create_rule_validates_arguments(my_courses, kwargs, message):
    args = {"course_ids": [COURSE], "instructions": "q", "request": "r", **kwargs}

    with pytest.raises(ToolError, match=message):
        tools.create_rule(**args)


def test_list_rules_reports_a_fresh_heartbeat_as_running():
    with closing(store.connect()) as conn:
        store.heartbeat(conn, "0.2.0")

    result = tools.list_rules()

    assert result["listener_running"] is True
    assert "hint" not in result


def test_ntfy_server_can_be_self_hosted(monkeypatch):
    monkeypatch.setenv("ED_LISTENER_NTFY_SERVER", "https://ntfy.example.com/")
    monkeypatch.setenv("ED_LISTENER_NTFY_TOPIC", "topic")

    assert tools.list_rules()["ntfy_subscribe_url"] == "https://ntfy.example.com/topic"


def test_delete_rule(my_courses):
    rule_id = tools.create_rule([COURSE], "q", "r")["rule"]["id"]

    assert tools.delete_rule(rule_id) == {"deleted": rule_id}
    with pytest.raises(ToolError, match="No rule"):
        tools.delete_rule(rule_id)


def _notified(conn, key, rules, title, scores):
    """Record what the daemon records for a post that notified."""
    store.record_judgments(conn, key, scores, "tev1:0.8b", title, f"https://ed/{key}")
    store.record_match(conn, key, rules, {"title": title, "message": "m", "url": f"https://ed/{key}"})
    store.mark_sent(conn, key, ok=True)


def test_list_notifications_shows_what_fired_with_scores():
    with closing(store.connect()) as conn:
        rule = store.add_rule(conn, "extensions", "q", [COURSE], False, "always")
        _notified(conn, "thread:1", [rule], "CS 101: HW3 extended", {rule["id"]: 0.93})

    result = tools.list_notifications()

    [notification] = result["notifications"]
    assert notification["title"] == "CS 101: HW3 extended"
    assert notification["url"] == "https://ed/thread:1"
    assert notification["delivered"] is True
    assert notification["matched"] == [{"rule_id": rule["id"], "request": "extensions", "score": 0.93}]
    assert "near_misses" not in result


def test_list_notifications_near_misses_are_just_below_the_threshold():
    with closing(store.connect()) as conn:
        rule = store.add_rule(conn, "extensions", "q", [COURSE], False, "always", 0.8)
        for key, score in [("thread:1", 0.75), ("thread:2", 0.55), ("thread:3", 0.62)]:
            store.record_judgments(conn, key, {rule["id"]: score}, "tev1:0.8b", f"post {key}", f"https://ed/{key}")

    result = tools.list_notifications(include_near_misses=True)

    assert [(n["title"], n["score"], n["threshold"]) for n in result["near_misses"]] == [
        ("post thread:1", 0.75, 0.8),
        ("post thread:3", 0.62, 0.8),
    ]


def test_list_notifications_filters_by_rule_and_labels_deleted_rules():
    with closing(store.connect()) as conn:
        a = store.add_rule(conn, "a", "q", [COURSE], False, "always")
        b = store.add_rule(conn, "b", "q", [COURSE], False, "always")
        _notified(conn, "thread:1", [a], "one", {a["id"]: 0.9})
        _notified(conn, "thread:2", [b], "two", {b["id"]: 0.9})
        store.delete_rule(conn, b["id"])

    assert [n["title"] for n in tools.list_notifications(rule_id=a["id"])["notifications"]] == ["one"]
    two = next(n for n in tools.list_notifications()["notifications"] if n["title"] == "two")
    assert two["matched"][0]["request"] == "(deleted rule)"


def test_list_notifications_defaults_to_the_last_week():
    with closing(store.connect()) as conn:
        rule = store.add_rule(conn, "a", "q", [COURSE], False, "always")
        _notified(conn, "thread:1", [rule], "old", {rule["id"]: 0.9})
        conn.execute("UPDATE sent SET at = '2026-01-01T00:00:00+00:00'")
        conn.commit()

    assert tools.list_notifications()["notifications"] == []
    assert [n["title"] for n in tools.list_notifications(since="2025-12-01")["notifications"]] == ["old"]


def test_old_databases_gain_the_judgment_title_columns(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE judgments (event_key TEXT NOT NULL, rule_id INTEGER NOT NULL, score REAL NOT NULL,"
                " model TEXT NOT NULL, at TEXT NOT NULL)")
    old.close()
    monkeypatch.setenv("ED_LISTENER_DB", str(path))

    with closing(store.connect()) as conn:
        store.record_judgments(conn, "thread:1", {1: 0.5}, "m", "title", "url")
        assert store.judgments_since(conn, "2000")[0]["title"] == "title"
