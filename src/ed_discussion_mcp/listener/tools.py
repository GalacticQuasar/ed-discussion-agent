"""MCP tools for the semantic listener, registered only when ED_LISTENER=1.

They never write to Ed; rule tools (coming) only write the listener's local
database. See server.main() for where register() is called.
"""

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .. import server
from . import judge

PREVIEW_MAX = 50
# Provisional: on a handful of hand-labelled posts, well-worded rules put
# true matches at 0.9+ and everything else at 0.73 or below.
DEFAULT_THRESHOLD = 0.8

INSTRUCTIONS = """
Notifications (listener tools):
- To set up a notification for new posts ("tell me if a deadline changes"),
  write the rule as a yes/no question about one post, then check it with
  preview_rule against recent threads before saving it.
- Scores come from a small local model, so wording matters: describe the
  event, not the topic ("Does this post say that a deadline was moved,
  extended or postponed?", not "Is this about deadlines?").
"""

PREVIEW = ToolAnnotations(read_only_hint=True, open_world_hint=True)


def preview_rule(
    course_id: int,
    instructions: str,
    staff_only: bool = False,
    threshold: float = DEFAULT_THRESHOLD,
    limit: int = 20,
) -> dict:
    """Score a draft notification rule against a course's most recent threads.

    instructions: a yes/no question about one post, answered by a small local
    model. Describe the event that should trigger a notification, as something
    the post itself says ("Does this post say that a deadline was moved,
    extended or postponed?"). Topic questions ("Is this about deadlines?")
    and broad ones match too much.
    staff_only: only consider posts by course staff. Use it for anything staff
    announce (deadline changes, recordings, exam details), since students
    asking about the same thing can otherwise score high.
    threshold: the score at or above which a post would notify.
    limit: how many recent threads to score (each takes about a second).

    Returns every scored thread, highest score first. Check that the top
    scores are posts the user wants and that unwanted posts (especially
    students asking about the topic) score clearly lower. If they don't,
    reword the question and preview again, or pick a threshold in the gap.
    Only thread posts are scored here, not replies.
    """
    limit = max(1, min(limit, PREVIEW_MAX))
    threads = []
    for thread in server._iter_threads(course_id, max_pages=3):
        summary = server._summary(thread)
        if staff_only and not summary["is_staff"]:
            continue
        threads.append((thread, summary))
        if len(threads) >= limit:
            break

    scored = []
    for thread, summary in threads:
        post = {
            "title": thread["title"],
            "category": thread["category"],
            "author_role": summary["author_role"],
            "body": server._text(thread),
        }
        try:
            score = judge.score(post, [(0, instructions)])[0]
        except judge.JudgeError as e:
            raise ToolError(str(e)) from e
        scored.append({
            "score": round(score, 3),
            "would_notify": score >= threshold,
            "title": summary["title"],
            "author_role": summary["author_role"],
            "is_staff": summary["is_staff"],
            "created_at": summary["created_at"],
            "url": summary["url"],
        })
    scored.sort(key=lambda s: s["score"], reverse=True)
    return {
        "model": judge.model(),
        "threshold": threshold,
        "scored": len(scored),
        "would_notify": sum(s["would_notify"] for s in scored),
        "threads": scored,
    }


def register(mcp):
    """Add the listener tools and instructions to the server."""
    mcp.add_tool(preview_rule, annotations=PREVIEW)
    # MCPServer has no public setter for instructions.
    mcp._lowlevel_server.instructions = (mcp.instructions or "") + INSTRUCTIONS
