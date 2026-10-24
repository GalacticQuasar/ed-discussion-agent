"""The MCP tools and the helpers that shape their results.

These replace server._get with a fake Ed, so they test what the tools request
and return, not HTTP or auth (see test_server_call.py for those).
"""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from ed_discussion_mcp import ed_api, server

COURSE = 92707


class FakeEd:
    """Stands in for server._get: serves a course's threads and records each request."""

    def __init__(self, monkeypatch, threads=(), routes=None):
        self.threads = list(threads)
        self.routes = routes or {}
        self.requests = []
        monkeypatch.setattr(server, "_get", self.get)

    def get(self, path, params=None):
        self.requests.append((path, params))
        if path == f"/courses/{COURSE}/threads":
            offset, limit = params["offset"], params["limit"]
            return {"threads": self.threads[offset:offset + limit]}
        return self.routes[path]

    def thread_pages(self):
        """Offsets of the thread-listing pages requested, in order."""
        return [params["offset"] for path, params in self.requests if path.endswith("/threads")]


def make_thread(id=1, **overrides):
    thread = {
        "id": id,
        "number": id,
        "course_id": COURSE,
        "user_id": 10,
        "title": f"Thread {id}",
        "type": "question",
        "category": "General",
        "subcategory": "",
        "user": {"id": 10, "name": "Student", "course_role": "student"},
        "created_at": "2026-10-10T12:00:00.000000+11:00",
        "updated_at": "2026-10-10T12:00:00.000000+11:00",
        "editor_id": None,
        "is_pinned": False,
        "is_answered": False,
        "is_staff_answered": False,
        "reply_count": 0,
        "document": f"Body of thread {id}",
        "content": "",
    }
    thread.update(overrides)
    return thread


def make_reply(id, user_id=10, **overrides):
    reply = {
        "id": id,
        "type": "answer",
        "user_id": user_id,
        "created_at": "2026-10-11T09:00:00.000000+11:00",
        "editor_id": None,
        "is_endorsed": False,
        "is_anonymous": False,
        "document": f"Reply {id}",
        "comments": [],
    }
    reply.update(overrides)
    return reply


def numbered_threads(count, **overrides):
    """count threads with ids 1..count, newest first as Ed lists them."""
    return [make_thread(i, **overrides) for i in range(1, count + 1)]


# --- _text ---

def test_text_prefers_document():
    assert server._text({"document": "  plain text \n", "content": "<p>xml</p>"}) == "plain text"


def test_text_falls_back_to_content_xml():
    post = {"document": "", "content": "<document><paragraph>Due <b>Fri</b> &amp; Sat</paragraph>\n\n<paragraph>ok</paragraph></document>"}
    assert server._text(post) == "Due Fri & Sat ok"


def test_text_of_empty_post():
    assert server._text({"document": None, "content": None}) == ""


# --- _snippet ---

def test_short_snippet_is_whole_text_with_whitespace_collapsed():
    assert server._snippet("a\n\n  b") == "a b"


def test_long_snippet_without_query_is_the_start():
    text = "x" * (server.SNIPPET_CHARS + 50)
    assert server._snippet(text) == "x" * server.SNIPPET_CHARS + "..."


def test_long_snippet_centres_on_first_query_term():
    text = "a" * 1000 + " midterm " + "b" * 1000
    snippet = server._snippet(text, "Exam MIDTERM")
    assert snippet.startswith("...") and snippet.endswith("...")
    assert "midterm" in snippet
    assert snippet.index("midterm") == 3 + server.SNIPPET_CHARS // 3


def test_long_snippet_with_unmatched_query_is_the_start():
    text = "a" * 1000
    assert server._snippet(text, "midterm") == "a" * server.SNIPPET_CHARS + "..."


def test_snippet_near_the_end_has_no_trailing_ellipsis():
    text = "a" * 1000 + " midterm"
    snippet = server._snippet(text, "midterm")
    assert snippet.startswith("...")
    assert snippet.endswith("midterm")


# --- _author ---

@pytest.mark.parametrize("user, expected", [
    (None, {"author": "Anonymous", "author_role": None, "is_staff": False}),
    ({"name": "Sam", "course_role": "student"}, {"author": "Sam", "author_role": "student", "is_staff": False}),
    ({"name": "Sam"}, {"author": "Sam", "author_role": None, "is_staff": False}),
    ({"name": "Pat", "course_role": "admin"}, {"author": "Pat", "author_role": "admin", "is_staff": True}),
    ({"name": "Pat", "course_role": "tutor"}, {"author": "Pat", "author_role": "tutor", "is_staff": True}),
], ids=["anonymous", "student", "no-role", "admin", "tutor"])
def test_author(user, expected):
    assert server._author(user) == expected


# --- _summary ---

def test_summary_fields():
    thread = make_thread(
        5, number=42, subcategory="", editor_id=7, is_pinned=True, is_answered=True,
        is_staff_answered=True, reply_count=3, type="announcement", category="Exams",
    )
    assert server._summary(thread) == {
        "id": 5,
        "number": 42,
        "course_id": COURSE,
        "title": "Thread 5",
        "type": "announcement",
        "category": "Exams",
        "subcategory": None,
        "author": "Student",
        "author_role": "student",
        "is_staff": False,
        "created_at": thread["created_at"],
        "updated_at": thread["updated_at"],
        "edited": True,
        "is_pinned": True,
        "is_answered": True,
        "is_staff_answered": True,
        "reply_count": 3,
        "url": f"https://edstem.org/us/courses/{COURSE}/discussion/5",
        "snippet": "Body of thread 5",
    }


def test_summary_keeps_subcategory_and_uses_region_in_url(monkeypatch):
    monkeypatch.setenv("ED_REGION", "au")
    summary = server._summary(make_thread(5, subcategory="Week 1"))
    assert summary["subcategory"] == "Week 1"
    assert summary["edited"] is False
    assert summary["url"] == f"https://edstem.org/au/courses/{COURSE}/discussion/5"


def test_summary_of_anonymous_thread():
    assert server._summary(make_thread(user=None))["author"] == "Anonymous"


# --- _reply ---

def test_reply_nests_comments_and_marks_accepted_answer():
    users = {10: {"name": "Sam", "course_role": "student"}, 20: {"name": "Pat", "course_role": "admin"}}
    reply = make_reply(
        1, user_id=20, is_endorsed=True, editor_id=20,
        comments=[make_reply(2, type="comment", comments=[make_reply(3, user_id=20, type="comment")])],
    )

    result = server._reply(reply, users, accepted_id=1)

    assert result == {
        "id": 1,
        "type": "answer",
        "author": "Pat",
        "author_role": "admin",
        "is_staff": True,
        "created_at": reply["created_at"],
        "edited": True,
        "is_endorsed": True,
        "is_accepted_answer": True,
        "text": "Reply 1",
        "replies": [{
            "id": 2,
            "type": "comment",
            "author": "Sam",
            "author_role": "student",
            "is_staff": False,
            "created_at": reply["created_at"],
            "edited": False,
            "is_endorsed": False,
            "is_accepted_answer": False,
            "text": "Reply 2",
            "replies": [server._reply(make_reply(3, user_id=20, type="comment"), users, 1)],
        }],
    }


def test_anonymous_reply_hides_author():
    users = {20: {"name": "Pat", "course_role": "admin"}}
    result = server._reply(make_reply(1, user_id=20, is_anonymous=True), users, None)
    assert (result["author"], result["is_staff"]) == ("Anonymous", False)


def test_reply_from_unknown_user_is_anonymous():
    assert server._reply(make_reply(1, user_id=99), {}, None)["author"] == "Anonymous"


# --- _parse_since ---

def test_parse_since():
    assert server._parse_since(None) is None
    assert server._parse_since("") is None
    assert server._parse_since("2026-10-01").isoformat() == "2026-10-01T00:00:00+00:00"
    assert server._parse_since("2026-10-01T09:00:00+11:00").isoformat() == "2026-10-01T09:00:00+11:00"
    assert server._parse_since("2026-10-01T09:00:00Z").isoformat() == "2026-10-01T09:00:00+00:00"


@pytest.mark.parametrize("since", ["Oct 1", "2026-13-01", "yesterday"])
def test_invalid_since_is_a_tool_error(monkeypatch, since):
    ed = FakeEd(monkeypatch)
    with pytest.raises(ToolError, match="Invalid since"):
        server.list_threads(COURSE, since=since)
    assert ed.requests == []


# --- _iter_threads ---

def test_iter_threads_pages_until_a_short_page(monkeypatch):
    ed = FakeEd(monkeypatch, numbered_threads(250))

    assert [t["id"] for t in server._iter_threads(COURSE)] == list(range(1, 251))
    assert ed.thread_pages() == [0, 100, 200]
    assert ed.requests[0] == (f"/courses/{COURSE}/threads", {"limit": 100, "offset": 0, "sort": "new"})


def test_iter_threads_requests_one_more_page_after_a_full_one(monkeypatch):
    ed = FakeEd(monkeypatch, numbered_threads(200))
    assert len(list(server._iter_threads(COURSE))) == 200
    assert ed.thread_pages() == [0, 100, 200]


def test_iter_threads_stops_at_max_pages(monkeypatch):
    ed = FakeEd(monkeypatch, numbered_threads(500))
    assert len(list(server._iter_threads(COURSE, max_pages=2))) == 200
    assert ed.thread_pages() == [0, 100]


def test_iter_threads_passes_category(monkeypatch):
    ed = FakeEd(monkeypatch, numbered_threads(3))
    list(server._iter_threads(COURSE, category="Homework"))
    assert ed.requests[0][1]["category"] == "Homework"


# --- list_courses ---

def make_course(id, active):
    return {
        "id": id, "code": f"CS{id}", "name": f"Course {id}", "year": "2026",
        "session": "Fall", "role": "student", "dashboard_active": active, "extra": "dropped",
    }


@pytest.fixture
def two_courses(monkeypatch):
    monkeypatch.setattr(ed_api, "get_token", lambda: ("tok", False))
    monkeypatch.setattr(ed_api, "get_courses", lambda token: [make_course(1, True), make_course(2, False)])


def test_list_courses_hides_archived(two_courses):
    assert server.list_courses() == [{
        "id": 1, "code": "CS1", "name": "Course 1", "year": "2026",
        "session": "Fall", "role": "student", "active": True,
    }]


def test_list_courses_includes_archived_on_request(two_courses):
    courses = server.list_courses(include_archived=True)
    assert [(c["id"], c["active"]) for c in courses] == [(1, True), (2, False)]


# --- course_overview ---

def course_route(settings):
    return {f"/courses/{COURSE}": {"course": {
        "id": COURSE, "code": "CS101", "name": "Intro", "year": "2026", "session": "Fall", "settings": settings,
    }}}


def test_course_overview(monkeypatch):
    threads = numbered_threads(30)
    threads[0]["is_pinned"] = True
    threads[20]["is_pinned"] = True
    for t in threads[5:20]:
        t["type"] = "announcement"
    settings = {"discussion": {"categories": [
        {"name": "General", "subcategories": []},
        {"name": "Homework", "subcategories": [{"name": "HW1"}, {"name": "HW2"}]},
        {"name": "Exams"},
    ]}}
    FakeEd(monkeypatch, threads, course_route(settings))

    overview = server.course_overview(COURSE)

    assert overview["course"] == {
        "id": COURSE, "code": "CS101", "name": "Intro", "year": "2026", "session": "Fall",
        "categories": [
            {"name": "General", "subcategories": []},
            {"name": "Homework", "subcategories": ["HW1", "HW2"]},
            {"name": "Exams", "subcategories": []},
        ],
    }
    assert overview["threads_scanned"] == 30
    assert [t["id"] for t in overview["pinned"]] == [1, 21]
    assert [t["id"] for t in overview["recent_announcements"]] == list(range(6, 16))
    assert [t["id"] for t in overview["recent_threads"]] == list(range(1, 11))


@pytest.mark.parametrize("settings", [None, {}, {"discussion": {}}, {"discussion": {"categories": None}}])
def test_course_overview_without_categories(monkeypatch, settings):
    FakeEd(monkeypatch, [], course_route(settings))
    overview = server.course_overview(COURSE)
    assert overview["course"]["categories"] == []
    assert overview["threads_scanned"] == 0


def test_course_overview_scans_at_most_three_pages(monkeypatch):
    ed = FakeEd(monkeypatch, numbered_threads(450), course_route({}))
    assert server.course_overview(COURSE)["threads_scanned"] == 300
    assert ed.thread_pages() == [0, 100, 200]


# --- search_threads ---

SEARCH_PATH = f"/courses/{COURSE}/threads/search"


def test_search_threads(monkeypatch):
    long_body = "a" * 1000 + " extension " + "b" * 1000
    ed = FakeEd(monkeypatch, routes={SEARCH_PATH: {"total": 57, "threads": [make_thread(3, document=long_body)]}})

    result = server.search_threads(COURSE, "hw3 extension")

    assert ed.requests == [(SEARCH_PATH, {"query": "hw3 extension", "limit": 20, "offset": 0})]
    assert result["total"] == 57
    assert [t["id"] for t in result["threads"]] == [3]
    assert "extension" in result["threads"][0]["snippet"]
    assert result["threads"][0]["snippet"].startswith("...")


@pytest.mark.parametrize("limit, offset, sent_limit, sent_offset", [
    (0, -5, 1, 0),
    (500, 40, 50, 40),
])
def test_search_threads_clamps_limit_and_offset(monkeypatch, limit, offset, sent_limit, sent_offset):
    ed = FakeEd(monkeypatch, routes={SEARCH_PATH: {"threads": []}})
    server.search_threads(COURSE, "midterm", limit=limit, offset=offset)
    params = ed.requests[0][1]
    assert (params["limit"], params["offset"]) == (sent_limit, sent_offset)


def test_search_threads_passes_category_and_tolerates_missing_total(monkeypatch):
    ed = FakeEd(monkeypatch, routes={SEARCH_PATH: {"threads": []}})
    assert server.search_threads(COURSE, "midterm", category="Exams") == {"total": None, "threads": []}
    assert ed.requests[0][1]["category"] == "Exams"


# --- list_threads ---

STAFF = {"id": 20, "name": "Pat", "course_role": "admin"}


def ids(threads):
    return [t["id"] for t in threads]


def test_list_threads_defaults_to_30(monkeypatch):
    ed = FakeEd(monkeypatch, numbered_threads(250))
    assert ids(server.list_threads(COURSE)) == list(range(1, 31))
    # Stops paging once it has enough.
    assert ed.thread_pages() == [0]


@pytest.mark.parametrize("limit, expected", [(0, 1), (5, 5), (1000, 100)])
def test_list_threads_clamps_limit(monkeypatch, limit, expected):
    FakeEd(monkeypatch, numbered_threads(250))
    assert len(server.list_threads(COURSE, limit=limit)) == expected


def test_list_threads_passes_category(monkeypatch):
    ed = FakeEd(monkeypatch, numbered_threads(3))
    server.list_threads(COURSE, category="Homework")
    assert ed.requests[0][1]["category"] == "Homework"


def test_list_threads_filters_by_type_pinned_and_staff(monkeypatch):
    threads = [
        make_thread(1, type="announcement", user=STAFF, is_pinned=True),
        make_thread(2, type="announcement", user=STAFF),
        make_thread(3, type="question"),
        make_thread(4, type="post", user=STAFF, is_pinned=True),
        make_thread(5, type="announcement", user=None),
    ]
    FakeEd(monkeypatch, threads)

    assert ids(server.list_threads(COURSE, thread_type="announcement")) == [1, 2, 5]
    assert ids(server.list_threads(COURSE, pinned_only=True)) == [1, 4]
    assert ids(server.list_threads(COURSE, staff_only=True)) == [1, 2, 4]
    assert ids(server.list_threads(COURSE, thread_type="announcement", staff_only=True, pinned_only=True)) == [1]


def test_list_threads_filters_keep_paging_to_fill_the_limit(monkeypatch):
    threads = numbered_threads(250)
    threads[150]["user"] = STAFF
    threads[249]["user"] = STAFF
    ed = FakeEd(monkeypatch, threads)

    assert ids(server.list_threads(COURSE, staff_only=True)) == [151, 250]
    assert ed.thread_pages() == [0, 100, 200]


def test_list_threads_since_stops_at_first_older_thread(monkeypatch):
    threads = numbered_threads(250)
    for i, t in enumerate(threads):
        day = 30 - i // 10  # ten threads per day, newest first
        t["created_at"] = f"2026-09-{day:02d}T12:00:00.000000+00:00"
    ed = FakeEd(monkeypatch, threads)

    found = server.list_threads(COURSE, since="2026-09-28", limit=100)

    assert ids(found) == list(range(1, 31))
    # Doesn't page past the cutoff.
    assert ed.thread_pages() == [0]


def test_list_threads_since_skips_old_pinned_threads(monkeypatch):
    threads = [
        make_thread(1, is_pinned=True, created_at="2026-01-01T00:00:00.000000+00:00"),
        make_thread(2, created_at="2026-10-05T00:00:00.000000+00:00"),
        make_thread(3, created_at="2026-10-02T00:00:00.000000+00:00"),
        make_thread(4, created_at="2026-09-20T00:00:00.000000+00:00"),
        make_thread(5, created_at="2026-10-03T00:00:00.000000+00:00"),
    ]
    FakeEd(monkeypatch, threads)
    assert ids(server.list_threads(COURSE, since="2026-10-01")) == [2, 3]


def test_list_threads_since_compares_across_timezones(monkeypatch):
    # 2026-10-01T05:00+11:00 is 2026-09-30T18:00 UTC, before a UTC midnight cutoff.
    threads = [
        make_thread(1, created_at="2026-10-01T13:00:00.000000+11:00"),
        make_thread(2, created_at="2026-10-01T05:00:00.000000+11:00"),
    ]
    FakeEd(monkeypatch, threads)
    assert ids(server.list_threads(COURSE, since="2026-10-01")) == [1]
    assert ids(server.list_threads(COURSE, since="2026-10-01T00:00:00+11:00")) == [1, 2]


def test_list_threads_returns_summaries(monkeypatch):
    FakeEd(monkeypatch, [make_thread(1)])
    assert server.list_threads(COURSE) == [server._summary(make_thread(1))]


# --- get_thread ---

def full_thread(**overrides):
    users = [
        {"id": 10, "name": "Sam", "course_role": "student"},
        {"id": 20, "name": "Pat", "course_role": "admin"},
    ]
    thread = make_thread(
        7, number=12, user=None, accepted_id=101, content="<document>Original</document>", document="",
        answers=[make_reply(101, user_id=20, comments=[make_reply(102, type="comment")])],
        comments=[make_reply(103, type="comment", user_id=20)],
    )
    thread.update(overrides)
    return {"thread": thread, "users": users}


def test_get_thread_by_id(monkeypatch):
    ed = FakeEd(monkeypatch, routes={"/threads/7": full_thread()})

    result = server.get_thread(thread_id=7)

    assert ed.requests == [("/threads/7", None)]
    assert "snippet" not in result
    assert (result["id"], result["number"], result["title"]) == (7, 12, "Thread 7")
    assert result["url"] == f"https://edstem.org/us/courses/{COURSE}/discussion/7"
    assert result["body"] == "Original"
    # The thread's author is looked up in the users list.
    assert (result["author"], result["is_staff"]) == ("Sam", False)

    [answer] = result["answers"]
    assert (answer["id"], answer["author"], answer["is_staff"], answer["is_accepted_answer"]) == (101, "Pat", True, True)
    assert [(c["id"], c["author"], c["is_accepted_answer"]) for c in answer["replies"]] == [(102, "Sam", False)]
    assert [(c["id"], c["author"]) for c in result["comments"]] == [(103, "Pat")]


def test_get_thread_by_course_and_number(monkeypatch):
    ed = FakeEd(monkeypatch, routes={f"/courses/{COURSE}/threads/12": full_thread()})
    assert server.get_thread(course_id=COURSE, number=12)["id"] == 7
    assert ed.requests == [(f"/courses/{COURSE}/threads/12", None)]


def test_get_thread_prefers_thread_id(monkeypatch):
    ed = FakeEd(monkeypatch, routes={"/threads/7": full_thread()})
    server.get_thread(thread_id=7, course_id=COURSE, number=12)
    assert ed.requests == [("/threads/7", None)]


@pytest.mark.parametrize("kwargs", [{}, {"course_id": COURSE}, {"number": 12}])
def test_get_thread_needs_id_or_course_and_number(monkeypatch, kwargs):
    ed = FakeEd(monkeypatch)
    with pytest.raises(ToolError, match="Pass thread_id"):
        server.get_thread(**kwargs)
    assert ed.requests == []


def test_get_thread_keeps_anonymous_author_hidden(monkeypatch):
    FakeEd(monkeypatch, routes={"/threads/7": full_thread(is_anonymous=True)})
    assert server.get_thread(thread_id=7)["author"] == "Anonymous"


def test_get_thread_keeps_embedded_user(monkeypatch):
    staff = {"id": 30, "name": "Lee", "course_role": "mentor"}
    FakeEd(monkeypatch, routes={"/threads/7": full_thread(user=staff)})
    result = server.get_thread(thread_id=7)
    assert (result["author"], result["is_staff"]) == ("Lee", True)


def test_get_thread_without_replies_or_users(monkeypatch):
    data = full_thread(answers=None, comments=None)
    del data["users"]
    FakeEd(monkeypatch, routes={"/threads/7": data})
    result = server.get_thread(thread_id=7)
    assert (result["author"], result["answers"], result["comments"]) == ("Anonymous", [], [])
