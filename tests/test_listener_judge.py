"""listener.judge: building System One state and requests, length retries, and errors.

The Ollama client is replaced with a fake; nothing talks to a real model.
"""

import ollama
import pytest

from ed_discussion_mcp.listener import judge


class FakeClient:
    """Records systemone calls. Answers with a fixed probability, or raises the queued errors first."""

    def __init__(self, noul=0.9):
        self.noul = noul
        self.errors = []
        self.calls = []

    def systemone(self, model, state, questions, keep_alive=None):
        self.calls.append({"model": model, "state": state, "questions": questions, "keep_alive": keep_alive})
        if self.errors:
            raise self.errors.pop(0)
        return ollama.SystemOneResponse(
            model=model,
            answers={key: {"type": "noul", "noul": self.noul} for key in questions},
            usage={"input_tokens": 1, "output_tokens": 1},
        )


def _too_long(used=2546, limit=2050):
    return ollama.ResponseError(f"prompt 0 has {used} tokens; expected 1–{limit} (input is never truncated)", 400)


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(judge, "_client", lambda: fake)
    return fake


def test_post_state_for_a_thread():
    state = judge.post_state({"title": "Week 7 recording", "category": "Lectures", "author_role": "admin", "body": " Link "})

    assert state == {"post": {"kind": "thread", "title": "Week 7 recording", "category": "Lectures", "author_role": "admin", "body": "Link"}}


def test_post_state_for_a_reply_includes_its_thread():
    state = judge.post_state({"body": "It's up now", "author_role": "staff"}, parent={"title": "Week 7 recording?", "body": "When?"})

    assert state["post"] == {"kind": "reply", "author_role": "staff", "body": "It's up now"}
    assert state["thread"] == {"title": "Week 7 recording?", "body": "When?"}


def test_post_state_defaults_missing_role_to_student_and_truncates():
    state = judge.post_state({"body": "x" * (judge.BODY_CHARS + 10)})

    assert state["post"]["author_role"] == "student"
    assert state["post"]["body"] == "x" * judge.BODY_CHARS + "..."


def test_score_asks_one_rule_per_request(client):
    scores = judge.score({"title": "t"}, [(1, "Is it a deadline change?"), (7, "Is it a recording?")])

    assert scores == {1: 0.9, 7: 0.9}
    assert [c["questions"] for c in client.calls] == [
        {"q": {"type": "noul", "instructions": "Is it a deadline change?"}},
        {"q": {"type": "noul", "instructions": "Is it a recording?"}},
    ]
    assert client.calls[0]["model"] == "tev1:0.8b"
    assert client.calls[0]["state"]["post"]["title"] == "t"


def test_score_uses_configured_model(client, monkeypatch):
    monkeypatch.setenv("ED_LISTENER_MODEL", "clef:cloud")

    judge.score({}, [(1, "q")])

    assert client.calls[0]["model"] == "clef:cloud"


def test_score_keeps_the_model_loaded_briefly_by_default(client):
    judge.score({}, [(1, "q")])

    assert client.calls[0]["keep_alive"] == "30s"


@pytest.mark.parametrize("value, sent", [("2m", "2m"), ("10", 10.0), ("0", 0.0)])
def test_score_uses_configured_keep_alive(client, monkeypatch, value, sent):
    monkeypatch.setenv("ED_LISTENER_KEEP_ALIVE", value)

    judge.score({}, [(1, "q")])

    assert client.calls[0]["keep_alive"] == sent


def test_score_without_rules_skips_the_request(client):
    assert judge.score({}, []) == {}
    assert client.calls == []


def test_too_long_post_is_retried_shorter_and_stays_shorter(client):
    client.errors = [_too_long()]
    post = {"body": "x" * 10000}

    scores = judge.score(post, [(1, "a"), (2, "b")])

    assert scores == {1: 0.9, 2: 0.9}
    bodies = [len(c["state"]["post"]["body"]) for c in client.calls]
    assert bodies[0] > bodies[1] == bodies[2]
    assert bodies[1] < judge.BODY_CHARS * 2050 / 2546


def test_still_too_long_after_retry_raises(client):
    client.errors = [_too_long(), _too_long()]

    with pytest.raises(judge.JudgeError, match="2546 tokens"):
        judge.score({"body": "x"}, [(1, "q")])


def test_missing_model_suggests_pulling_it(client):
    client.errors = [ollama.ResponseError("model 'tev1:0.8b' not found", 404)]

    with pytest.raises(judge.JudgeError, match=r"ollama pull tev1:0\.8b"):
        judge.score({}, [(1, "q")])


def test_unreachable_ollama_raises_judge_error(client):
    client.errors = [ConnectionError("Failed to connect to Ollama.")]

    with pytest.raises(judge.JudgeError, match="Failed to connect"):
        judge.score({}, [(1, "q")])
