"""listener.daemon: turning Ed events into judged, deduplicated notifications.

Ed (server._get), the judge and ntfy are faked; the database is a temporary
file (see conftest). The websocket itself isn't exercised.
"""

import asyncio
from contextlib import closing

import pytest

from ed_discussion_mcp.listener import daemon, judge, notify, store
from test_server_tools import COURSE, make_thread

STAFF = {"id": 20, "name": "TA", "course_role": "staff"}
STUDENT = {"id": 10, "name": "Student", "course_role": "student"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def conn():
    with closing(store.connect()) as conn:
        yield conn


@pytest.fixture
def ed(monkeypatch):
    """Threads served by id from /threads/{id}, and the user's courses."""
    threads = {}
    requests = []

    def get(path, params=None):
        requests.append(path)
        if path.startswith("/threads/"):
            thread = threads[int(path.rsplit("/", 1)[1])]
            return {"thread": thread, "users": [STAFF, STUDENT]}
        if path == f"/courses/{COURSE}/threads":
            listing = sorted(threads.values(), key=lambda t: t["created_at"], reverse=True)
            return {"threads": listing[params["offset"]:params["offset"] + params["limit"]]}
        raise AssertionError(f"unexpected request {path}")

    monkeypatch.setattr(daemon.server, "_get", get)
    monkeypatch.setattr(daemon.server, "_call", lambda fn: [{"id": COURSE, "code": "CS 101"}])
    return type("Ed", (), {"threads": threads, "requests": requests})


@pytest.fixture
def scores(monkeypatch):
    """judge.score returns table[(title, rule_id)] (default 0.0) and records each call."""
    table = {}
    calls = []

    def score(post, rules, parent=None):
        calls.append({"post": post, "rules": rules, "parent": parent})
        return {rule_id: table.get((post.get("title") or parent["title"], rule_id), 0.0) for rule_id, _ in rules}

    monkeypatch.setattr(judge, "score", score)
    return type("Scores", (), {"table": table, "calls": calls})


@pytest.fixture
def sent(monkeypatch):
    """Notifications sent to ntfy; set .ok = False to make sends fail."""
    box = type("Sent", (), {"items": [], "ok": True})

    def send(topic, notification):
        box.items.append((topic, notification))
        return box.ok

    monkeypatch.setattr(notify, "send_ntfy", send)
    return box


def add_rule(conn, request="tell me about extensions", staff_only=False, mode="always", threshold=None, courses=(COURSE,)):
    return store.add_rule(conn, request, f"Q: {request}", list(courses), staff_only, mode, threshold)


def listener(conn):
    lis = daemon.Listener(conn)
    lis.course_codes = {COURSE: "CS 101"}
    lis.subscribed = lis.rule_courses()
    return lis


def staff_thread(id, title, **overrides):
    return make_thread(id, title=title, user=STAFF, user_id=20, **overrides)


def test_matching_thread_notifies_once_with_rule_and_link(conn, ed, scores, sent):
    rule = add_rule(conn)
    ed.threads[1] = staff_thread(1, "HW3 extended", document="HW3 is now due Friday.")
    scores.table[("HW3 extended", rule["id"])] = 0.95
    lis = listener(conn)

    run(lis.on_message({"type": "thread.new", "data": {"thread": {"id": 1}}}))
    run(lis.on_message({"type": "thread.new", "data": {"thread": {"id": 1}}}))

    [(topic, notification)] = sent.items
    assert topic == store.ntfy_topic(conn)
    assert notification["title"] == "CS 101: HW3 extended"
    assert notification["message"].startswith("Matched: tell me about extensions\n\nHW3 is now due Friday.")
    assert notification["url"].endswith(f"/courses/{COURSE}/discussion/1")
    assert len(scores.calls) == 1
    assert scores.calls[0]["post"]["author_role"] == "staff"
    assert store.unsent(conn, "9999") == []


def test_below_threshold_records_the_score_but_does_not_notify(conn, ed, scores, sent):
    rule = add_rule(conn, threshold=0.9)
    ed.threads[1] = staff_thread(1, "HW3 released")
    scores.table[("HW3 released", rule["id"])] = 0.85

    run(listener(conn).on_thread(1))

    assert sent.items == []
    [row] = conn.execute("SELECT * FROM judgments").fetchall()
    assert (row["event_key"], row["rule_id"], row["score"]) == ("thread:1", rule["id"], 0.85)


def test_one_notification_lists_every_matched_rule(conn, ed, scores, sent):
    a = add_rule(conn, "extensions")
    b = add_rule(conn, "anything about HW3")
    ed.threads[1] = staff_thread(1, "HW3 extended")
    scores.table[("HW3 extended", a["id"])] = scores.table[("HW3 extended", b["id"])] = 0.9

    run(listener(conn).on_thread(1))

    [(_, notification)] = sent.items
    assert notification["message"].startswith("Matched: extensions; anything about HW3")


def test_once_rule_is_disabled_after_its_first_match(conn, ed, scores, sent):
    rule = add_rule(conn, "week 7 recording", mode="once")
    ed.threads[1] = staff_thread(1, "Week 7 recording")
    ed.threads[2] = staff_thread(2, "Week 7 recording (again)")
    scores.table[("Week 7 recording", rule["id"])] = scores.table[("Week 7 recording (again)", rule["id"])] = 0.99
    lis = listener(conn)

    run(lis.on_thread(1))
    run(lis.on_thread(2))

    assert len(sent.items) == 1
    assert store.get_rule(conn, rule["id"])["enabled"] is False
    assert store.get_rule(conn, rule["id"])["fired_at"]


def test_staff_only_rules_skip_student_posts_without_scoring(conn, ed, scores, sent):
    add_rule(conn, staff_only=True)
    ed.threads[1] = make_thread(1, title="Will HW3 be extended?")

    run(listener(conn).on_thread(1))

    assert scores.calls == []


def test_rules_only_apply_to_their_courses(conn, ed, scores, sent):
    add_rule(conn, courses=(999,))
    ed.threads[1] = staff_thread(1, "HW3 extended")

    run(listener(conn).on_thread(1))

    assert scores.calls == []


def test_student_replies_are_ignored_without_fetching(conn, ed, scores, sent):
    add_rule(conn)

    run(listener(conn).on_message({"type": "comment.new", "data": {"comment": {
        "id": 5, "thread_id": 1, "course_id": COURSE, "document": "+1", "user": STUDENT,
    }}}))

    assert ed.requests == []
    assert scores.calls == []


def test_staff_reply_is_judged_with_its_thread_and_links_to_the_comment(conn, ed, scores, sent):
    rule = add_rule(conn)
    ed.threads[1] = make_thread(1, title="Week 7 recording?", document="When will it be up?")
    scores.table[("Week 7 recording?", rule["id"])] = 0.9

    run(listener(conn).on_message({"type": "comment.new", "data": {"comment": {
        "id": 5, "thread_id": 1, "course_id": COURSE, "type": "answer", "document": "It's up now.", "user": STAFF,
    }}}))

    [call] = scores.calls
    assert call["post"]["body"] == "It's up now."
    assert call["parent"] == {"title": "Week 7 recording?", "category": "General", "body": "When will it be up?"}
    [(_, notification)] = sent.items
    assert notification["title"] == 'CS 101: staff reply in "Week 7 recording?"'
    assert notification["url"].endswith("/discussion/1?comment=5")


def test_private_posts_are_sent_without_their_body(conn, ed, scores, sent):
    rule = add_rule(conn)
    ed.threads[1] = staff_thread(1, "Your extension", is_private=True, document="Secret details")
    scores.table[("Your extension", rule["id"])] = 0.9

    run(listener(conn).on_thread(1))

    [(_, notification)] = sent.items
    assert notification["message"] == "Matched: tell me about extensions"


def test_private_posts_are_not_sent_to_cloud_models(conn, ed, scores, sent, monkeypatch):
    add_rule(conn)
    monkeypatch.setenv("ED_LISTENER_MODEL", "clef:cloud")
    ed.threads[1] = staff_thread(1, "Your extension", is_private=True)

    run(listener(conn).on_thread(1))

    assert scores.calls == []


def test_failed_send_is_retried_by_tick(conn, ed, scores, sent):
    rule = add_rule(conn)
    ed.threads[1] = staff_thread(1, "HW3 extended")
    scores.table[("HW3 extended", rule["id"])] = 0.9
    lis = listener(conn)
    sent.ok = False

    run(lis.on_thread(1))
    sent.ok = True
    run(lis.tick())

    assert len(sent.items) == 2
    assert sent.items[0] == sent.items[1]
    assert store.unsent(conn, "9999") == []
    assert store.last_heartbeat(conn)


def test_tick_reconnects_when_a_rule_names_a_new_course(conn):
    add_rule(conn)
    lis = listener(conn)

    run(lis.tick())
    assert not lis.reconnect.is_set()

    add_rule(conn, courses=(2,))
    run(lis.tick())
    assert lis.reconnect.is_set()


def test_first_backfill_starts_from_now(conn, ed, scores, sent):
    add_rule(conn)
    ed.threads[1] = staff_thread(1, "Old post")

    run(listener(conn).backfill())

    assert scores.calls == []
    assert store.get_cursor(conn, COURSE)


def test_backfill_processes_missed_threads_oldest_first(conn, ed, scores, sent):
    add_rule(conn)
    store.advance_cursor(conn, COURSE, "2026-10-10T12:00:00+00:00")
    ed.threads[1] = staff_thread(1, "Before", created_at="2026-10-09T12:00:00+00:00")
    ed.threads[2] = staff_thread(2, "Missed A", created_at="2026-10-11T12:00:00+00:00")
    ed.threads[3] = staff_thread(3, "Missed B", created_at="2026-10-12T12:00:00+00:00")

    run(listener(conn).backfill())

    assert [c["post"]["title"] for c in scores.calls] == ["Missed A", "Missed B"]
    assert store.get_cursor(conn, COURSE) == "2026-10-12T12:00:00+00:00"
    # The listing already has each thread's author, so nothing is fetched again.
    assert not [path for path in ed.requests if path.startswith("/threads/")]
