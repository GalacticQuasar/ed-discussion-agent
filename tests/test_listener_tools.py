"""listener.tools: preview_rule and registration behind ED_LISTENER.

Ed is faked as in test_server_tools.py, and judge.score is replaced, so no
model or network is involved.
"""

import asyncio

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ed_discussion_mcp.listener import judge, tools
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

    assert "preview_rule" in [t.name for t in asyncio.run(mcp.list_tools())]
    assert mcp.instructions.startswith("Base.")
    assert "preview_rule" in mcp.instructions
