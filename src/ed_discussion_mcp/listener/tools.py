"""MCP tools for the semantic listener, registered only when ED_LISTENER=1.

They never write to Ed; the rule tools only write the listener's local
database (see store), which the `listen` daemon reads. See server.main() for
where register() is called.
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Literal

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .. import ed_api, server
from . import judge, notify, store

PREVIEW_MAX = 50

HEARTBEAT_STALE = timedelta(minutes=3)  # the daemon writes one every minute
LISTEN_COMMAND = "uvx --from 'ed-discussion-mcp[listener]' ed-discussion-mcp listen"

INSTRUCTIONS = """
Notifications (listener tools):
- To set up a notification for new posts ("tell me if a deadline changes"),
  write the rule as a yes/no question about one post, check it with
  preview_rule against recent threads, then save it with create_rule.
- Scores come from a small local model, so wording matters: describe the
  event, not the topic ("Does this post say that a deadline was moved,
  extended or postponed?", not "Is this about deadlines?").
- Use mode="once" for a one-time event ("when the week 7 recording is
  posted") and "always" for standing rules ("any deadline change").
- After creating a rule, tell the user the ntfy subscribe link and whether
  the listener is running (both in the create_rule and list_rules results).
- list_notifications shows what rules caught recently. To tune a rule that
  misses posts or fires too often, look at its near misses and the scores of
  what it caught, then adjust the threshold or wording (delete and recreate).
"""

PREVIEW = ToolAnnotations(read_only_hint=True, open_world_hint=True)
LOCAL_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
LOCAL_DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)
LOCAL_READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def preview_rule(
    course_id: int,
    instructions: str,
    staff_only: bool = False,
    threshold: float = judge.DEFAULT_THRESHOLD,
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
    reword the question and preview again. When there is a clear gap, pass
    a threshold inside it to create_rule: wanted posts can score as low as
    0.7-0.8, so e.g. 0.7 is reasonable if every unwanted post scores well
    below it. Only thread posts are scored here, not replies. Private threads
    are skipped on a cloud model, as the listener skips them.
    """
    limit = max(1, min(limit, PREVIEW_MAX))
    threads = []
    skipped_private = 0
    for thread in server._iter_threads(course_id, max_pages=3):
        summary = server._summary(thread)
        if staff_only and not summary["is_staff"]:
            continue
        if thread.get("is_private") and not judge.may_judge_private():
            skipped_private += 1
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
    result = {
        "model": judge.model(),
        "threshold": threshold,
        "scored": len(scored),
        "would_notify": sum(s["would_notify"] for s in scored),
        "threads": scored,
    }
    if skipped_private:
        result["skipped_private"] = skipped_private
    return result


def _status(conn):
    """Whether the daemon is running, and where notifications go."""
    beat = store.last_heartbeat(conn)
    running = bool(beat) and datetime.now(timezone.utc) - datetime.fromisoformat(beat["at"]) < HEARTBEAT_STALE
    status = {
        "listener_running": running,
        "last_heartbeat": beat["at"] if beat else None,
        "ntfy_subscribe_url": notify.ntfy_subscribe_url(store.ntfy_topic(conn)),
    }
    if not running:
        status["hint"] = (
            "The listener isn't running, so rules won't fire. Start it with: "
            f"{LISTEN_COMMAND} (it needs the same ED_API_TOKEN and Ollama)."
        )
    return status


def _rule_view(rule):
    return {
        **rule,
        "threshold": judge.threshold(rule),
    }


def create_rule(
    course_ids: list[int],
    instructions: str,
    request: str,
    mode: Literal["once", "always"] = "always",
    staff_only: bool = False,
    threshold: float | None = None,
) -> dict:
    """Save a notification rule: new posts in course_ids that score at or
    above threshold on instructions notify the user (via ntfy).

    Preview the instructions with preview_rule first; see its description for
    how to word them. request is the user's own words ("notify me if a
    deadline changes"), shown in notifications. mode "once" disables the rule
    after its first match; "always" keeps it. threshold defaults to 0.8.
    New threads and staff replies are checked; with staff_only, only posts by
    course staff.
    """
    if not instructions.strip() or not request.strip():
        raise ToolError("instructions and request must not be empty.")
    if not course_ids:
        raise ToolError("Pass at least one course_id (see list_courses).")
    if threshold is not None and not 0 < threshold < 1:
        raise ToolError("threshold must be between 0 and 1.")
    known = {c["id"] for c in server._call(ed_api.get_courses)}
    unknown = sorted(set(course_ids) - known)
    if unknown:
        raise ToolError(f"Not your courses: {unknown} (see list_courses).")
    with closing(store.connect()) as conn:
        rule = store.add_rule(conn, request.strip(), instructions.strip(), course_ids, staff_only, mode, threshold)
        return {"rule": _rule_view(rule), **_status(conn)}


def list_rules() -> dict:
    """List the user's notification rules, whether the listener is running,
    and the ntfy link where notifications arrive."""
    # Marked read-only: it may generate the ntfy topic, but only once.
    with closing(store.connect()) as conn:
        return {"rules": [_rule_view(r) for r in store.list_rules(conn)], **_status(conn)}


NEAR_MISS_MARGIN = 0.2


def list_notifications(
    since: str | None = None,
    rule_id: int | None = None,
    include_near_misses: bool = False,
    limit: int = 50,
) -> dict:
    """Notifications the listener sent, newest first, with the rules each matched.

    since: ISO date or datetime (default: the last 7 days).
    rule_id: only notifications (and near misses) for this rule.
    include_near_misses: also return posts that scored below a rule's
    threshold by less than 0.2, which shows what a rule almost caught and
    helps choose a threshold. Each comes with its score and the rule's threshold.
    """
    since_dt = server._parse_since(since) or datetime.now(timezone.utc) - timedelta(days=7)
    since_iso = since_dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    limit = max(1, min(limit, 200))
    with closing(store.connect()) as conn:
        rules = {r["id"]: r for r in store.list_rules(conn)}

        def rule_view(rid):
            rule = rules.get(rid)
            return {"rule_id": rid, "request": rule["request"] if rule else "(deleted rule)"}

        scores = {(j["event_key"], j["rule_id"]): j["score"] for j in reversed(store.judgments_since(conn, since_iso))}
        notifications = []
        for row in store.sent_since(conn, since_iso):
            if rule_id is not None and rule_id not in row["rule_ids"]:
                continue
            notifications.append({
                "title": row["notification"]["title"],
                "url": row["notification"]["url"],
                "at": row["at"],
                "delivered": row["status"] == "sent",
                "matched": [{**rule_view(r), "score": scores.get((row["event_key"], r))} for r in row["rule_ids"]],
            })
        result = {"since": since_iso, "notifications": notifications[:limit]}

        if include_near_misses:
            near, seen = [], set()
            for j in store.judgments_since(conn, since_iso):
                rule = rules.get(j["rule_id"])
                key = (j["event_key"], j["rule_id"])
                if not rule or key in seen or (rule_id is not None and j["rule_id"] != rule_id):
                    continue
                seen.add(key)  # newest score per post and rule (a retried post can be scored twice)
                threshold = judge.threshold(rule)
                if threshold - NEAR_MISS_MARGIN <= j["score"] < threshold:
                    near.append({
                        "title": j["title"],
                        "url": j["url"],
                        "at": j["at"],
                        **rule_view(j["rule_id"]),
                        "score": round(j["score"], 3),
                        "threshold": threshold,
                    })
            near.sort(key=lambda n: n["score"], reverse=True)
            result["near_misses"] = near[:limit]
        return result



def delete_rule(rule_id: int) -> dict:
    """Delete a notification rule by id (see list_rules)."""
    with closing(store.connect()) as conn:
        if not store.delete_rule(conn, rule_id):
            raise ToolError(f"No rule {rule_id} (see list_rules).")
    return {"deleted": rule_id}


def register(mcp):
    """Add the listener tools and instructions to the server."""
    mcp.add_tool(preview_rule, annotations=PREVIEW)
    mcp.add_tool(create_rule, annotations=LOCAL_WRITE)
    mcp.add_tool(list_rules, annotations=LOCAL_READ)
    mcp.add_tool(list_notifications, annotations=LOCAL_READ)
    mcp.add_tool(delete_rule, annotations=LOCAL_DELETE)
    # MCPServer has no public setter for instructions.
    mcp._lowlevel_server.instructions = (mcp.instructions or "") + INSTRUCTIONS
