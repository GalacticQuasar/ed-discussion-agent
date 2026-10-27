"""Score Ed posts against listener rules with an Ollama System One model.

Each rule is a `noul` (yes/no) question, and its answer is the probability
of yes. Scores aren't calibrated, which is why each rule has a threshold.

Rules are asked one per request: with several questions in one request,
tev1 lets a true answer to one raise the others (a "final exam date" post
scored 0.10 on a lecture-recording rule alone and 0.98 batched).

Configuration comes from environment variables at call time:
ED_LISTENER_MODEL (default "tev1:0.8b") and OLLAMA_HOST (read by the SDK).
"""

import os
import re

import ollama

DEFAULT_MODEL = "tev1:0.8b"
# tev1 has a 2050-token context and System One never truncates input, so
# bodies are cut to fit. Too long anyway, and score() retries shorter.
BODY_CHARS = 3000
PARENT_BODY_CHARS = 1000
_TOO_LONG = re.compile(r"has (\d+) tokens; expected 1\D(\d+)")


class JudgeError(Exception):
    """System One couldn't score a post (Ollama unreachable, model missing, bad request)."""


def model():
    return os.environ.get("ED_LISTENER_MODEL") or DEFAULT_MODEL


def _client():
    return ollama.Client()


def _truncate(text, limit):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "..."


def post_state(post, parent=None, body_chars=BODY_CHARS, parent_chars=PARENT_BODY_CHARS):
    """The `state` System One sees for a post.

    post and parent are dicts with title, category, author_role and body. For a
    reply, parent is its thread: "It's up now" means nothing without the title.
    """
    state = {
        "post": {
            "kind": "reply" if parent else "thread",
            "title": post.get("title"),
            "category": post.get("category"),
            "author_role": post.get("author_role") or "student",
            "body": _truncate(post.get("body"), body_chars),
        }
    }
    if parent:
        state["thread"] = {
            "title": parent.get("title"),
            "category": parent.get("category"),
            "body": _truncate(parent.get("body"), parent_chars),
        }
    return {k: {f: v for f, v in d.items() if v is not None} for k, d in state.items()}


def _ask(client, name, state, instructions):
    response = client.systemone(model=name, state=state, questions={"q": {"type": "noul", "instructions": instructions}})
    return response.answers["q"].noul


def score(post, rules, parent=None):
    """Score post (see post_state) against rules, a list of (rule_id, instructions).

    Returns {rule_id: probability of yes}.
    """
    client = _client()
    name = model()
    body_chars, parent_chars = BODY_CHARS, PARENT_BODY_CHARS
    scores = {}
    for rule_id, instructions in rules:
        try:
            try:
                scores[rule_id] = _ask(client, name, post_state(post, parent, body_chars, parent_chars), instructions)
            except ollama.ResponseError as e:
                match = _TOO_LONG.search(e.error)
                if not match:
                    raise
                # Shrink both bodies in proportion, with a margin for the
                # non-body tokens, and keep the shorter size for later rules.
                used, limit = map(int, match.groups())
                ratio = limit / used * 0.8
                body_chars, parent_chars = int(body_chars * ratio), int(parent_chars * ratio)
                scores[rule_id] = _ask(client, name, post_state(post, parent, body_chars, parent_chars), instructions)
        except ConnectionError as e:
            raise JudgeError(f"{e} (OLLAMA_HOST={os.environ.get('OLLAMA_HOST') or 'default'})") from e
        except ollama.ResponseError as e:
            hint = f" (run `ollama pull {name}`)" if e.status_code == 404 else ""
            raise JudgeError(f"System One request failed: {e.error}{hint}") from e
    return scores
