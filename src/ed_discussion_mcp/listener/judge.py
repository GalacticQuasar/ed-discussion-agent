"""Score Ed posts against listener rules with an Ollama System One model.

Each rule is a `noul` (yes/no) question, and its answer is the probability
of yes. Scores aren't calibrated, which is why each rule has a threshold.

Rules are asked one per request: with several questions in one request,
tev1 lets a true answer to one raise the others (a "final exam date" post
scored 0.10 on a lecture-recording rule alone and 0.98 batched).

Configuration comes from environment variables at call time:
ED_LISTENER_MODEL (default "tev1:0.8b"), ED_LISTENER_KEEP_ALIVE (default "30s")
and OLLAMA_HOST (read by the SDK).
"""

import os
import re

import ollama

DEFAULT_MODEL = "tev1:0.8b"
# How long Ollama keeps the model loaded after each request (its default is
# 5 minutes). Posts are minutes apart, and the timer restarts on every
# request, so this only needs to bridge the requests for one post or preview.
DEFAULT_KEEP_ALIVE = "30s"
# Provisional, biased toward recall: in the first eval (see todo.md) true
# matches scored 0.93+ but some false ones reached ~0.9.
DEFAULT_THRESHOLD = 0.8
# System One never truncates input, so bodies are cut to fit the model's
# context: about 5000 tokens of English here, within tev1's 8192 (Ollama
# 0.40.11's build). Older builds had 2050; on a length error score() retries shorter.
BODY_CHARS = 16000
PARENT_BODY_CHARS = 4000
_TOO_LONG = re.compile(r"has (\d+) tokens; expected 1\D(\d+)")


class JudgeError(Exception):
    """System One couldn't score a post (Ollama unreachable, model missing, bad request)."""


def model():
    return os.environ.get("ED_LISTENER_MODEL") or DEFAULT_MODEL


def keep_alive():
    """ED_LISTENER_KEEP_ALIVE as Ollama takes it: a duration ("30s", "1m") or seconds."""
    value = os.environ.get("ED_LISTENER_KEEP_ALIVE") or DEFAULT_KEEP_ALIVE
    try:
        return float(value)
    except ValueError:
        return value


def threshold(rule):
    """A rule's threshold, or the default when it has none."""
    return rule["threshold"] if rule.get("threshold") is not None else DEFAULT_THRESHOLD


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
    response = client.systemone(
        model=name,
        state=state,
        questions={"q": {"type": "noul", "instructions": instructions}},
        keep_alive=keep_alive(),
    )
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


def is_cloud():
    """Whether the configured model runs on Ollama's servers rather than locally."""
    return model().endswith(("-cloud", ":cloud"))


def check_model():
    """Raise JudgeError unless the configured model can answer a System One question.

    Asks a real question: `ollama show` succeeds even for a download that the
    installed Ollama can no longer load.
    """
    score({"title": "Startup check"}, [(0, "Is this a startup check?")])
