"""The `ed-discussion-mcp listen` daemon: judge new Ed posts against rules and notify.

Watches the courses that enabled rules name. New threads and staff replies are
scored with judge (one request per rule), and a post scoring at or above a
rule's threshold sends one ntfy notification listing every rule it matched.

On every (re)connect, threads created since each course's cursor are
backfilled. Replies posted while disconnected are not (yet). Rules are read
from the database for each post, so new rules apply without a restart; a rule
naming a new course triggers a reconnect within HEARTBEAT_SECONDS.

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

from .. import __version__, ed_api, server
from . import judge, notify, store
from .stream import listen

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 60
PENDING_RETRY_AFTER = timedelta(minutes=5)
SEEN_MAX = 10000
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


class Listener:
    def __init__(self, conn):
        self.conn = conn
        self.topic = store.ntfy_topic(conn)
        self.course_codes = {}
        self.subscribed = set()
        self.reconnect = asyncio.Event()
        self._seen = OrderedDict()
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

    def _first_time(self, key):
        """True the first time a key is seen this run (live and backfill can overlap)."""
        if key in self._seen:
            return False
        self._seen[key] = None
        if len(self._seen) > SEEN_MAX:
            self._seen.popitem(last=False)
        return True

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
        key = f"thread:{thread_id}"
        if not self._first_time(key):
            return
        if listed:
            thread, users = listed, {}
        else:
            data = await asyncio.to_thread(server._get, f"/threads/{thread_id}")
            thread = data["thread"]
            users = {u["id"]: u for u in data.get("users") or []}
        user = None if thread.get("is_anonymous") else thread.get("user") or users.get(thread["user_id"])
        event = {
            "key": key,
            "course_id": thread["course_id"],
            "kind": "thread",
            "is_staff": server._author(user)["is_staff"],
            "is_private": bool(thread.get("is_private")),
            "post": _post(thread, user),
            "parent": None,
            "url": ed_api.thread_url(thread["course_id"], thread["id"]),
        }
        await self.process(event)
        store.advance_cursor(self.conn, thread["course_id"], thread["created_at"])

    async def on_comment(self, comment):
        if not comment.get("id"):
            return
        user = comment.get("user")
        # Staff stay staff when posting anonymously (anonymity hides them from students only).
        if not server._author(user)["is_staff"]:
            return
        key = f"comment:{comment['id']}"
        if not self._first_time(key) or comment.get("course_id") not in self.rule_courses():
            return
        data = await asyncio.to_thread(server._get, f"/threads/{comment['thread_id']}")
        thread = data["thread"]
        event = {
            "key": key,
            "course_id": comment["course_id"],
            "kind": "reply",
            "is_staff": True,
            "is_private": bool(comment.get("is_private") or thread.get("is_private")),
            "post": _post(comment, user),
            "parent": {"title": thread.get("title"), "category": thread.get("category"), "body": server._text(thread)},
            "url": comment_url(comment["course_id"], comment["thread_id"], comment["id"]),
        }
        await self.process(event)

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
        store.record_judgments(self.conn, event["key"], scores, judge.model())
        matched = [r for r in rules if scores[r["id"]] >= judge.threshold(r)]
        log.info("%s scored %s", event["key"], {r: round(s, 2) for r, s in scores.items()})
        if not matched:
            return
        notification = self.notification(event, matched)
        if store.record_match(self.conn, event["key"], matched, notification):
            await self.send(event["key"], notification)

    def notification(self, event, rules):
        code = self.course_codes.get(event["course_id"], str(event["course_id"]))
        if event["kind"] == "reply":
            title = f"{code}: staff reply in \"{event['parent']['title']}\""
        else:
            title = f"{code}: {event['post']['title']}"
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
        """Process threads created since each watched course's cursor, oldest first.

        A course seen for the first time starts from now, so creating a rule
        never notifies about old posts.
        """
        async with self._backfilling:  # a reconnect mid-backfill mustn't start a second one
            for course_id in sorted(self.subscribed):
                cursor = store.get_cursor(self.conn, course_id)
                if cursor is None:
                    store.advance_cursor(self.conn, course_id, store.now())
                    continue
                since = datetime.fromisoformat(cursor)
                missed = await asyncio.to_thread(self._threads_since, course_id, since)
                if missed:
                    log.info("Backfilling %d thread(s) in course %s", len(missed), course_id)
                for thread in reversed(missed):
                    try:
                        await self.on_thread(thread["id"], listed=thread)
                    except Exception:
                        log.exception("Backfill failed for thread %s", thread["id"])

    @staticmethod
    def _threads_since(course_id, since):
        missed = []
        for thread in server._iter_threads(course_id):
            if datetime.fromisoformat(thread["created_at"]) <= since:
                # Pinned threads can sort ahead of newer ones.
                if thread["is_pinned"]:
                    continue
                break
            missed.append(thread)
        return missed

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

    async def tick(self):
        """Heartbeat, retry unsent notifications, and reconnect if a rule names a new course."""
        store.heartbeat(self.conn, __version__)
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
