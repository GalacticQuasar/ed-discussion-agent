"""The `ed-discussion-mcp listen` daemon: judge new Ed posts against rules and notify.

Watches the courses that enabled rules name. New threads and staff replies are
scored with judge (one request per rule), and a post scoring at or above a
rule's threshold sends one ntfy notification listing every rule it matched.

On every (re)connect, each watched course is backfilled: threads created
since its cursor, and staff replies in threads whose reply count rose since it
was last recorded. A post whose handling fails (Ed or Ollama unreachable) is
queued and retried by tick() for up to RETRY_FOR. Rules are read from the
database for each post, so new rules apply without a restart; a rule naming a
new course triggers a reconnect within HEARTBEAT_SECONDS.

All database access stays on the event loop thread; Ed requests, scoring and
sending run in worker threads.
"""

import asyncio
import logging
import os
import sys
from collections import OrderedDict
from contextlib import closing
from datetime import datetime, timedelta, timezone

from mcp.server.mcpserver.exceptions import ToolError

from .. import __version__, ed_api, server
from . import judge, notify, store
from .stream import listen

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 60
PENDING_RETRY_AFTER = timedelta(minutes=5)
RETRY_FOR = timedelta(hours=24)
DONE_MAX = 10000
SNIPPET_CHARS = 300


def comment_url(course_id, thread_id, comment_id):
    return f"{ed_api.thread_url(course_id, thread_id)}?comment={comment_id}"


def _post(item, user):
    """The judge's view of a thread or reply."""
    return {
        "title": item.get("title"),
        "category": item.get("category"),
        "author_role": server._author(user)["author_role"],
        "body": server._text(item),
    }


def _replies(thread):
    """Every answer and comment in a fetched thread, nested ones included."""
    stack = list(thread.get("answers") or []) + list(thread.get("comments") or [])
    while stack:
        reply = stack.pop()
        yield reply
        stack.extend(reply.get("comments") or [])


def _with_user(reply, users):
    """A reply from a thread fetch carries only user_id; add the user, as stream events have."""
    return {**reply, "user": reply.get("user") or users.get(reply.get("user_id"))}


class PostGone(Exception):
    """The post was deleted, or is no longer visible."""


def _gone(error):
    """Whether retrying is pointless: the post is gone, or Ed says it's not found or forbidden."""
    cause = error.__cause__ if isinstance(error, ToolError) else error
    return isinstance(error, PostGone) or (isinstance(cause, ed_api.EdAPIError) and cause.status in (403, 404))


class Listener:
    def __init__(self, conn):
        self.conn = conn
        self.topic = store.ntfy_topic(conn)
        self.course_codes = {}
        self.subscribed = set()
        self.reconnect = asyncio.Event()
        self._done = OrderedDict()
        self._running = set()
        self._backfilling = asyncio.Lock()

    # Courses

    def rule_courses(self):
        return {c for rule in store.list_rules(self.conn, enabled_only=True) for c in rule["course_ids"]}

    def courses_to_watch(self):
        self.subscribed = self.rule_courses()
        if not self.subscribed:
            log.info("No enabled rules yet; waiting for create_rule")
        return sorted(self.subscribed)

    async def load_course_codes(self):
        courses = await asyncio.to_thread(server._call, ed_api.get_courses)
        self.course_codes = {c["id"]: c.get("code") or c.get("name") or str(c["id"]) for c in courses}

    # Events

    async def _handle(self, key, work, thread_id, comment_id=None):
        """Run work() once per post per run. A post counts as handled only when
        work() succeeds; on failure it's queued for tick() to retry."""
        if key in self._done or key in self._running:
            return
        self._running.add(key)
        try:
            await work()
        except Exception as e:
            if _gone(e):
                log.info("%s is no longer on Ed (%s); skipping", key, e)
                self._mark_done(key)
            else:
                log.warning("Couldn't handle %s (%s); will retry", key, e)
                store.add_retry(self.conn, key, thread_id, comment_id, str(e))
        else:
            self._mark_done(key)
        finally:
            self._running.discard(key)

    def _mark_done(self, key):
        store.remove_retry(self.conn, key)
        self._done[key] = None
        if len(self._done) > DONE_MAX:
            self._done.popitem(last=False)

    async def on_message(self, message):
        data = message.get("data") or {}
        if message.get("type") == "thread.new":
            thread = data.get("thread") or {}
            if thread.get("id"):
                await self.on_thread(thread["id"])
        elif message.get("type") == "comment.new":
            await self.on_comment(data.get("comment") or {})

    async def on_thread(self, thread_id, listed=None):
        """Judge a new thread. listed is the thread from a listing, which already
        has its author; otherwise it's fetched, since the live event's payload
        may lack the author's role."""
        await self._handle(f"thread:{thread_id}", lambda: self._judge_thread(thread_id, listed), thread_id)

    async def _judge_thread(self, thread_id, listed=None):
        if listed:
            thread, users = listed, {}
        else:
            data = await asyncio.to_thread(server._get, f"/threads/{thread_id}")
            thread = data["thread"]
            users = {u["id"]: u for u in data.get("users") or []}
        # Track its replies from now on, so backfill can find any posted while disconnected.
        store.set_reply_counts(self.conn, thread["course_id"], {thread["id"]: thread.get("reply_count") or 0},
                               store.now(), keep_existing=True)
        user = None if thread.get("is_anonymous") else thread.get("user") or users.get(thread["user_id"])
        await self.process({
            "key": f"thread:{thread_id}",
            "course_id": thread["course_id"],
            "kind": "thread",
            "is_staff": server._author(user)["is_staff"],
            "is_private": bool(thread.get("is_private")),
            "post": _post(thread, user),
            "parent": None,
            "url": ed_api.thread_url(thread["course_id"], thread["id"]),
        })
        store.advance_cursor(self.conn, thread["course_id"], thread["created_at"])

    async def on_comment(self, comment, thread_data=None):
        """Judge a staff reply. thread_data is its fetched thread, if already at hand."""
        if not comment.get("id"):
            return
        # Staff stay staff when posting anonymously (anonymity hides them from students only).
        if not server._author(comment.get("user"))["is_staff"]:
            return
        if comment.get("course_id") not in self.rule_courses():
            return
        await self._handle(f"comment:{comment['id']}", lambda: self._judge_comment(comment, thread_data),
                           comment["thread_id"], comment["id"])

    async def _judge_comment(self, comment, thread_data=None):
        data = thread_data or await asyncio.to_thread(server._get, f"/threads/{comment['thread_id']}")
        thread = data["thread"]
        await self.process({
            "key": f"comment:{comment['id']}",
            "course_id": comment["course_id"],
            "kind": "reply",
            "is_staff": True,
            "is_private": bool(comment.get("is_private") or thread.get("is_private")),
            "post": _post(comment, comment.get("user")),
            "parent": {"title": thread.get("title"), "category": thread.get("category"), "body": server._text(thread)},
            "url": comment_url(comment["course_id"], comment["thread_id"], comment["id"]),
        })

    async def _retry_comment(self, thread_id, comment_id):
        data = await asyncio.to_thread(server._get, f"/threads/{thread_id}")
        users = {u["id"]: u for u in data.get("users") or []}
        for reply in _replies(data["thread"]):
            if reply["id"] == comment_id:
                comment = _with_user(reply, users)
                comment.setdefault("thread_id", thread_id)
                comment.setdefault("course_id", data["thread"]["course_id"])
                return await self._judge_comment(comment, data)
        raise PostGone(f"comment {comment_id} is no longer in thread {thread_id}")

    def matching_rules(self, event):
        return [
            rule for rule in store.list_rules(self.conn, enabled_only=True)
            if event["course_id"] in rule["course_ids"] and (event["is_staff"] or not rule["staff_only"])
        ]

    async def process(self, event):
        if store.already_sent(self.conn, event["key"]):
            return
        rules = self.matching_rules(event)
        if not rules:
            return
        if event["is_private"] and judge.is_cloud() and os.environ.get("ED_LISTENER_CLOUD_PRIVATE") != "1":
            log.info("Skipping private %s: %s is a cloud model", event["key"], judge.model())
            return
        scores = await asyncio.to_thread(
            judge.score, event["post"], [(r["id"], r["instructions"]) for r in rules], event["parent"]
        )
        store.record_judgments(self.conn, event["key"], scores, judge.model(), self.title(event), event["url"])
        matched = [r for r in rules if scores[r["id"]] >= judge.threshold(r)]
        log.info("%s scored %s", event["key"], {r: round(s, 2) for r, s in scores.items()})
        if not matched:
            return
        notification = self.notification(event, matched)
        if store.record_match(self.conn, event["key"], matched, notification):
            await self.send(event["key"], notification)

    def title(self, event):
        code = self.course_codes.get(event["course_id"], str(event["course_id"]))
        if event["kind"] == "reply":
            return f"{code}: staff reply in \"{event['parent']['title']}\""
        return f"{code}: {event['post']['title']}"

    def notification(self, event, rules):
        title = self.title(event)
        message = "Matched: " + "; ".join(r["request"] for r in rules)
        # Private posts go out with their title only: ntfy topics are readable
        # by anyone who has the name.
        if not event["is_private"]:
            message += "\n\n" + server._snippet(event["post"]["body"] or "")[:SNIPPET_CHARS]
        return {"title": title, "message": message, "url": event["url"]}

    async def send(self, key, notification):
        ok = await asyncio.to_thread(notify.send_ntfy, self.topic, notification)
        store.mark_sent(self.conn, key, ok)
        log.info("Notified %s" if ok else "Notification for %s failed; will retry", key)

    # Backfill and housekeeping

    async def backfill(self):
        """Catch up on each watched course: threads created since its cursor
        (oldest first), then staff replies in threads whose reply count rose.

        A course seen for the first time starts from now, so creating a rule
        never notifies about old posts.
        """
        async with self._backfilling:  # a reconnect mid-backfill mustn't start a second one
            for course_id in sorted(self.subscribed):
                try:
                    await self._backfill_course(course_id)
                except Exception:
                    log.exception("Backfill failed for course %s", course_id)

    async def _backfill_course(self, course_id):
        listed_at = store.now()
        threads = await asyncio.to_thread(lambda: list(server._iter_threads(course_id)))
        cursor = store.get_cursor(self.conn, course_id)
        if cursor is None:
            store.advance_cursor(self.conn, course_id, listed_at)
            store.set_reply_counts(self.conn, course_id, {t["id"]: t["reply_count"] for t in threads}, listed_at)
            return
        since = datetime.fromisoformat(cursor)
        # Read before judging new threads, which records their counts as of now
        # and would hide replies posted to them while disconnected.
        known = store.reply_counts(self.conn, course_id)

        missed = [t for t in threads if datetime.fromisoformat(t["created_at"]) > since]
        if missed:
            log.info("Backfilling %d thread(s) in course %s", len(missed), course_id)
        for thread in sorted(missed, key=lambda t: t["created_at"]):
            await self.on_thread(thread["id"], listed=thread)

        unknown = {t["id"]: t["reply_count"] for t in threads if t["id"] not in known}
        for thread in threads:
            count, seen_at = known.get(thread["id"], (0, thread["created_at"]))
            if thread["id"] in unknown and datetime.fromisoformat(thread["created_at"]) <= since:
                continue  # existed before replies were tracked; start tracking from now
            if thread["reply_count"] > count:
                if await self._backfill_replies(thread["id"], seen_at):
                    store.set_reply_counts(self.conn, course_id, {thread["id"]: thread["reply_count"]}, listed_at)
        store.set_reply_counts(self.conn, course_id, unknown, listed_at, keep_existing=True)

    async def _backfill_replies(self, thread_id, since):
        """Judge staff replies in a thread posted after since. Returns whether the thread could be read."""
        try:
            data = await asyncio.to_thread(server._get, f"/threads/{thread_id}")
        except Exception as e:
            log.warning("Couldn't read thread %s to backfill replies (%s)", thread_id, e)
            return False
        thread = data["thread"]
        users = {u["id"]: u for u in data.get("users") or []}
        since = datetime.fromisoformat(since)
        new = [r for r in _replies(thread) if datetime.fromisoformat(r["created_at"]) > since]
        for reply in sorted(new, key=lambda r: r["created_at"]):
            comment = {**_with_user(reply, users), "thread_id": thread_id, "course_id": thread["course_id"]}
            await self.on_comment(comment, data)
        return True

    async def on_connect(self):
        await self.load_course_codes()
        # Run beside the stream so events keep flowing while it catches up.
        asyncio.ensure_future(self._guarded(self.backfill()))

    @staticmethod
    async def _guarded(coro):
        try:
            await coro
        except Exception:
            log.exception("Background task failed")

    async def retry_failed(self):
        """Retry posts whose handling failed, giving up after RETRY_FOR."""
        give_up_before = datetime.now(timezone.utc) - RETRY_FOR
        for retry in store.retries(self.conn):
            key, thread_id, comment_id = retry["event_key"], retry["thread_id"], retry["comment_id"]
            if datetime.fromisoformat(retry["first_failed_at"]) < give_up_before:
                log.warning("Giving up on %s after %d attempts: %s", key, retry["attempts"], retry["error"])
                store.remove_retry(self.conn, key)
            elif comment_id is None:
                await self._handle(key, lambda: self._judge_thread(thread_id), thread_id)
            else:
                await self._handle(key, lambda: self._retry_comment(thread_id, comment_id), thread_id, comment_id)

    async def tick(self):
        """Heartbeat, retry failed posts and unsent notifications, and reconnect
        if a rule names a new course."""
        store.heartbeat(self.conn, __version__)
        await self.retry_failed()
        cutoff = (datetime.now(timezone.utc) - PENDING_RETRY_AFTER).isoformat(timespec="seconds")
        for key, notification in store.unsent(self.conn, cutoff):
            await self.send(key, notification)
        if self.rule_courses() - self.subscribed:
            self.reconnect.set()

    async def housekeeping(self):
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("Housekeeping failed")
            await asyncio.sleep(HEARTBEAT_SECONDS)

    async def run(self):
        # Set before the first tick, which would otherwise see every rule's course as new.
        self.subscribed = self.rule_courses()
        housekeeping = asyncio.ensure_future(self.housekeeping())
        try:
            await listen(self.courses_to_watch, self.on_message, self.on_connect, self.reconnect)
        finally:
            housekeeping.cancel()


def main():
    """Entry point for `ed-discussion-mcp listen`."""
    log.info("Starting the listener (ed-discussion-mcp %s)", __version__)
    try:
        ed_api.get_token()
        judge.check_model()
    except (ed_api.MissingCredentialsError, ed_api.InvalidRegionError, judge.JudgeError) as e:
        raise SystemExit(f"Can't start the listener: {e}")
    with closing(store.connect()) as conn:
        url = notify.ntfy_subscribe_url(store.ntfy_topic(conn))
        _print_subscribe(url)
        log.info("Database %s, model %s", store.db_path(), judge.model())
        try:
            asyncio.run(Listener(conn).run())
        except KeyboardInterrupt:
            pass


def _print_subscribe(url):
    """Show where notifications go, with a QR code for the phone app when segno is installed."""
    print(f"Notifications go to {url}\nSubscribe in the ntfy app (or open the link) to receive them.")
    try:
        import segno

        segno.make(url, error="l").terminal(compact=True)
    except ImportError:
        pass
    # Flush now: when stdout is a file or pipe, the daemon may never exit cleanly.
    sys.stdout.flush()
