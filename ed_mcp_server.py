"""MCP server that gives an agent read-only access to your Ed Discussion courses.

Usage:
    python ed_mcp_server.py

Speaks MCP over stdio. Credentials come from .env via auth_flow.get_token()
(ED_API_TOKEN preferred, ED_EMAIL/ED_PASSWORD as fallback). Keyword search
uses Ed's own search endpoint, which matches thread titles, bodies and replies.
"""

import html
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

import auth_flow

log = logging.getLogger("ed_mcp_server")

API_BASE = "https://us.edstem.org/api"
WEB_BASE = "https://edstem.org/us"
PAGE_SIZE = 100  # Ed caps thread listings at 100 per request
SNIPPET_CHARS = 300
STUDENT_ROLES = {None, "student"}

INSTRUCTIONS = """\
Read-only access to the user's Ed Discussion (edstem.org) course forums.

Typical workflow: list_courses to find the course ID (match the user's wording
against code and name, preferring active courses), then search_threads or
list_threads to find relevant posts, then get_thread to read the full post
and its replies before answering.

Tips:
- Exam dates, locations and deadline changes usually live in announcements
  and staff posts. Check list_threads(thread_type="announcement") as well as
  search_threads, since a change may be posted under a different wording.
- When posts conflict, the newest staff post or staff reply wins. Report the
  date of the post you rely on.
- For "what should I know before homework N", look at the category for that
  homework (see course_overview) and read threads with staff answers or many
  replies; corrections and clarifications are often in replies, not the body.
- Cite the thread url for every fact you report.
"""

mcp = MCPServer("ed-discussion", instructions=INSTRUCTIONS)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)

_AUTH_HINT = " (Ed rejected the credentials; check ED_API_TOKEN, or ED_EMAIL/ED_PASSWORD, in .env)"
_STATUS_HINTS = {
    403: " (no access: check the course_id with list_courses)",
    404: " (not found: check the id or number)",
}

_token_lock = threading.Lock()
_token = None
_refreshable = False


def _call(fn):
    """Run fn(token), logging in lazily.

    Retries once with a fresh login after an auth error when the token came from
    email/password. A rejected static ED_API_TOKEN raises.
    """
    global _token, _refreshable
    try:
        with _token_lock:
            if _token is None:
                _token, _refreshable = auth_flow.get_token()
            token = _token
        try:
            return fn(token)
        except auth_flow.EdAPIError as e:
            if not e.is_auth_error or not _refreshable:
                raise
            log.info("Token rejected; logging in again")
            with _token_lock:
                _token = auth_flow.get_auth_token()
                token = _token
            return fn(token)
    # ToolError (unlike other exceptions) shows its message to the model.
    except auth_flow.MissingCredentialsError as e:
        raise ToolError(str(e)) from e
    except auth_flow.EdAPIError as e:
        hint = _AUTH_HINT if e.is_auth_error else _STATUS_HINTS.get(e.status, "")
        raise ToolError(f"{e}{hint}") from e


def _get(path, params=None):
    """GET an Ed API path and return the JSON body."""
    return _call(lambda token: auth_flow._authed_get(f"{API_BASE}{path}", token, params=params).json())


def _text(post):
    """Plain text of a thread or reply. Ed's 'document' field is already plain text; 'content' is XML."""
    if post.get("document"):
        return post["document"].strip()
    content = re.sub(r"<[^>]+>", " ", post.get("content") or "")
    return re.sub(r"\s+", " ", html.unescape(content)).strip()


def _snippet(text, query=None):
    """Up to SNIPPET_CHARS of text, centred on the first query term found when a query is given."""
    text = re.sub(r"\s+", " ", text)
    if len(text) <= SNIPPET_CHARS:
        return text
    start = 0
    if query:
        lowered = text.lower()
        hits = [i for i in (lowered.find(term) for term in query.lower().split()) if i >= 0]
        if hits:
            start = max(0, min(hits) - SNIPPET_CHARS // 3)
    snippet = text[start:start + SNIPPET_CHARS]
    return ("..." if start else "") + snippet + ("..." if start + SNIPPET_CHARS < len(text) else "")


def _author(user):
    if not user:
        return {"author": "Anonymous", "author_role": None, "is_staff": False}
    role = user.get("course_role")
    return {"author": user.get("name"), "author_role": role, "is_staff": role not in STUDENT_ROLES}


def _thread_url(thread):
    return f"{WEB_BASE}/courses/{thread['course_id']}/discussion/{thread['id']}"


def _summary(thread, query=None):
    """Compact view of a thread for listings and search results."""
    return {
        "id": thread["id"],
        "number": thread["number"],
        "course_id": thread["course_id"],
        "title": thread["title"],
        "type": thread["type"],
        "category": thread["category"],
        "subcategory": thread["subcategory"] or None,
        **_author(thread.get("user")),
        "created_at": thread["created_at"],
        "updated_at": thread["updated_at"],
        "edited": thread.get("editor_id") is not None,
        "is_pinned": thread["is_pinned"],
        "is_answered": thread["is_answered"],
        "is_staff_answered": thread["is_staff_answered"],
        "reply_count": thread["reply_count"],
        "url": _thread_url(thread),
        "snippet": _snippet(_text(thread), query),
    }


def _reply(reply, users, accepted_id):
    """A reply (answer or comment) with its nested comments."""
    return {
        "id": reply["id"],
        "type": reply["type"],
        **_author(None if reply.get("is_anonymous") else users.get(reply["user_id"])),
        "created_at": reply["created_at"],
        "edited": reply.get("editor_id") is not None,
        "is_endorsed": reply["is_endorsed"],
        "is_accepted_answer": reply["id"] == accepted_id,
        "text": _text(reply),
        "replies": [_reply(c, users, accepted_id) for c in reply.get("comments") or []],
    }


def _parse_since(since):
    if not since:
        return None
    parsed = datetime.fromisoformat(since)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iter_threads(course_id, category=None, max_pages=10):
    """Yield a course's threads newest first, paging through Ed's listing."""
    for page in range(max_pages):
        params = {"limit": PAGE_SIZE, "offset": page * PAGE_SIZE, "sort": "new"}
        if category:
            params["category"] = category
        threads = _get(f"/courses/{course_id}/threads", params)["threads"]
        yield from threads
        if len(threads) < PAGE_SIZE:
            return


def _course_info(course_id):
    """Name and discussion categories of one course."""
    course = _get(f"/courses/{course_id}")["course"]
    categories = (course.get("settings") or {}).get("discussion", {}).get("categories") or []
    return {
        "id": course["id"],
        "code": course.get("code"),
        "name": course.get("name"),
        "year": course.get("year"),
        "session": course.get("session"),
        "categories": [
            {"name": c["name"], "subcategories": [s["name"] for s in c.get("subcategories") or []]}
            for c in categories
        ],
    }


@mcp.tool(annotations=READ_ONLY)
def list_courses(include_archived: bool = False) -> list[dict]:
    """List the user's Ed courses in dashboard order.

    Active courses come first. Archived (past) courses are left out unless
    include_archived is true. Use the returned id as course_id in other tools.
    """
    return [
        {
            "id": c["id"],
            "code": c["code"],
            "name": c["name"],
            "year": c["year"],
            "session": c["session"],
            "role": c["role"],
            "active": c["dashboard_active"],
        }
        for c in _call(auth_flow.get_courses)
        if include_archived or c["dashboard_active"]
    ]


@mcp.tool(annotations=READ_ONLY)
def course_overview(course_id: int) -> dict:
    """Overview of a course: its discussion categories, pinned threads, recent
    announcements and most recent threads.

    Good first call for broad questions like "what's going on in my course".
    """
    threads = list(_iter_threads(course_id, max_pages=3))
    return {
        "course": _course_info(course_id),
        "threads_scanned": len(threads),
        "pinned": [_summary(t) for t in threads if t["is_pinned"]],
        "recent_announcements": [_summary(t) for t in threads if t["type"] == "announcement"][:10],
        "recent_threads": [_summary(t) for t in threads[:10]],
    }


@mcp.tool(annotations=READ_ONLY)
def search_threads(
    course_id: int,
    query: str,
    category: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Keyword search over a course's threads using Ed's own search, which
    matches titles, bodies and replies. Results are ordered by relevance.

    Keep queries short (one to three keywords, e.g. "midterm", "hw3 extension").
    Try synonyms if the first search misses ("exam" / "midterm" / "final").
    category restricts results to one discussion category (see course_overview).
    Use offset to page through more results; 'total' is the number of matches.
    """
    params = {"query": query, "limit": max(1, min(limit, 50)), "offset": max(0, offset)}
    if category:
        params["category"] = category
    result = _get(f"/courses/{course_id}/threads/search", params)
    return {
        "total": result.get("total"),
        "threads": [_summary(t, query) for t in result["threads"]],
    }


@mcp.tool(annotations=READ_ONLY)
def list_threads(
    course_id: int,
    category: str | None = None,
    thread_type: Literal["announcement", "question", "post"] | None = None,
    pinned_only: bool = False,
    staff_only: bool = False,
    since: str | None = None,
    limit: int = 30,
) -> list[dict]:
    """List a course's threads newest first, with optional filters.

    thread_type: only announcements, questions or posts.
    pinned_only: only pinned threads.
    staff_only: only threads written by course staff (instructors, TAs).
    since: ISO date or datetime (e.g. "2026-10-01"); only threads created on or after it.
    """
    since_dt = _parse_since(since)
    limit = max(1, min(limit, 100))
    found = []
    for thread in _iter_threads(course_id, category):
        if since_dt and datetime.fromisoformat(thread["created_at"]) < since_dt:
            # Pinned threads can sort ahead of newer ones, so skip rather than stop on those.
            if thread["is_pinned"]:
                continue
            break
        summary = _summary(thread)
        if thread_type and thread["type"] != thread_type:
            continue
        if pinned_only and not thread["is_pinned"]:
            continue
        if staff_only and not summary["is_staff"]:
            continue
        found.append(summary)
        if len(found) >= limit:
            break
    return found


@mcp.tool(annotations=READ_ONLY)
def get_thread(
    thread_id: int | None = None,
    course_id: int | None = None,
    number: int | None = None,
) -> dict:
    """Read a full thread: its body and every answer and comment, nested, with
    each author's role and whether a reply is endorsed or the accepted answer.

    Pass thread_id (the 'id' from other tools), or course_id plus number
    (the "#123" number shown in Ed's UI).
    """
    if thread_id is not None:
        data = _get(f"/threads/{thread_id}")
    elif course_id is not None and number is not None:
        data = _get(f"/courses/{course_id}/threads/{number}")
    else:
        raise ToolError("Pass thread_id, or both course_id and number.")
    thread = data["thread"]
    users = {u["id"]: u for u in data.get("users") or []}
    if not thread.get("user") and not thread.get("is_anonymous"):
        thread["user"] = users.get(thread["user_id"])
    accepted_id = thread.get("accepted_id")
    summary = _summary(thread)
    del summary["snippet"]
    return {
        **summary,
        "body": _text(thread),
        "answers": [_reply(r, users, accepted_id) for r in thread.get("answers") or []],
        "comments": [_reply(r, users, accepted_id) for r in thread.get("comments") or []],
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    mcp.run()
