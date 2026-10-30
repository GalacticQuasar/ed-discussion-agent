"""The listener's SQLite database, shared by the MCP server (rules) and the daemon (state).

The path is ED_LISTENER_DB, or a per-user data directory by default, so both
processes find the same file without matching configuration.
"""

import json
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    request TEXT NOT NULL,
    instructions TEXT NOT NULL,
    course_ids TEXT NOT NULL,
    staff_only INTEGER NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('once', 'always')),
    threshold REAL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    fired_at TEXT
);
CREATE TABLE IF NOT EXISTS cursors (course_id INTEGER PRIMARY KEY, last_seen_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS judgments (
    event_key TEXT NOT NULL,
    rule_id INTEGER NOT NULL,
    score REAL NOT NULL,
    model TEXT NOT NULL,
    at TEXT NOT NULL,
    title TEXT,  -- the post, for list_notifications' near misses
    url TEXT
);
CREATE TABLE IF NOT EXISTS sent (
    event_key TEXT PRIMARY KEY,
    rule_ids TEXT NOT NULL,
    notification TEXT NOT NULL,  -- JSON, kept so a failed send can be retried
    status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- Reply counts as of seen_at, to find threads that got replies while disconnected.
CREATE TABLE IF NOT EXISTS threads (
    thread_id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL,
    reply_count INTEGER NOT NULL,
    seen_at TEXT NOT NULL
);
-- Posts whose handling failed (Ed or Ollama unreachable), retried by the daemon.
CREATE TABLE IF NOT EXISTS retries (
    event_key TEXT PRIMARY KEY,
    thread_id INTEGER NOT NULL,
    comment_id INTEGER,
    error TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    first_failed_at TEXT NOT NULL
);
"""


def _data_dir():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "ed-discussion-mcp"


def db_path():
    return Path(os.environ.get("ED_LISTENER_DB") or _data_dir() / "listener.db")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL lets the daemon write while the MCP server reads.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Columns added after a table first shipped: (table, column, type).
_ADDED_COLUMNS = [("judgments", "title", "TEXT"), ("judgments", "url", "TEXT")]


def _migrate(conn):
    """Add columns that databases created by earlier versions lack."""
    for table, column, kind in _ADDED_COLUMNS:
        if column not in {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}:
            with conn:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def _rule(row):
    rule = dict(row)
    rule["course_ids"] = json.loads(rule["course_ids"])
    rule["staff_only"] = bool(rule["staff_only"])
    rule["enabled"] = bool(rule["enabled"])
    return rule


def add_rule(conn, request, instructions, course_ids, staff_only, mode, threshold=None):
    with conn:
        cursor = conn.execute(
            "INSERT INTO rules (request, instructions, course_ids, staff_only, mode, threshold, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (request, instructions, json.dumps(sorted(set(course_ids))), int(staff_only), mode, threshold, now()),
        )
    return get_rule(conn, cursor.lastrowid)


def get_rule(conn, rule_id):
    row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
    return _rule(row) if row else None


def list_rules(conn, enabled_only=False):
    sql = "SELECT * FROM rules" + (" WHERE enabled" if enabled_only else "") + " ORDER BY id"
    return [_rule(row) for row in conn.execute(sql)]


def delete_rule(conn, rule_id):
    with conn:
        return conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,)).rowcount > 0


def get_setting(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn, key, value):
    with conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def ntfy_topic(conn):
    """ED_LISTENER_NTFY_TOPIC, or a random topic generated once and kept in the database.

    On public ntfy.sh the topic name is the only access control, so it must be unguessable.
    """
    topic = os.environ.get("ED_LISTENER_NTFY_TOPIC")
    if topic:
        return topic
    topic = get_setting(conn, "ntfy_topic")
    if not topic:
        topic = f"ed-{secrets.token_urlsafe(18)}"
        with conn:
            # Another process may have generated one first; keep theirs.
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('ntfy_topic', ?)", (topic,))
        topic = get_setting(conn, "ntfy_topic")
    return topic


def heartbeat(conn, version):
    set_setting(conn, "heartbeat", json.dumps({"at": now(), "version": version}))


def last_heartbeat(conn):
    value = get_setting(conn, "heartbeat")
    return json.loads(value) if value else None


def get_cursor(conn, course_id):
    row = conn.execute("SELECT last_seen_at FROM cursors WHERE course_id = ?", (course_id,)).fetchone()
    return row["last_seen_at"] if row else None


def advance_cursor(conn, course_id, seen_at):
    """Move a course's cursor forward to seen_at (never back)."""
    current = get_cursor(conn, course_id)
    if current and datetime.fromisoformat(current) >= datetime.fromisoformat(seen_at):
        return
    with conn:
        conn.execute("INSERT OR REPLACE INTO cursors (course_id, last_seen_at) VALUES (?, ?)", (course_id, seen_at))


def record_judgments(conn, event_key, scores, model, title=None, url=None):
    at = now()
    with conn:
        conn.executemany(
            "INSERT INTO judgments (event_key, rule_id, score, model, at, title, url) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(event_key, rule_id, score, model, at, title, url) for rule_id, score in scores.items()],
        )


def judgments_since(conn, since):
    """Scores recorded at or after since (an ISO time), newest first."""
    rows = conn.execute("SELECT * FROM judgments WHERE at >= ? ORDER BY at DESC, rowid DESC", (since,))
    return [dict(row) for row in rows]


def sent_since(conn, since):
    """Notifications claimed at or after since (an ISO time), newest first."""
    rows = conn.execute("SELECT * FROM sent WHERE at >= ? ORDER BY at DESC, rowid DESC", (since,))
    return [
        {**dict(row), "rule_ids": json.loads(row["rule_ids"]), "notification": json.loads(row["notification"])}
        for row in rows
    ]


def already_sent(conn, event_key):
    return conn.execute("SELECT 1 FROM sent WHERE event_key = ?", (event_key,)).fetchone() is not None


def record_match(conn, event_key, rules, notification):
    """Record a match before notifying, in one transaction: claim event_key,
    and mark the rules fired (disabling "once" rules) so a crash between this
    and the send never notifies twice. Returns False if event_key was already claimed.
    """
    at = now()
    with conn:
        claimed = conn.execute(
            "INSERT OR IGNORE INTO sent (event_key, rule_ids, notification, status, at) VALUES (?, ?, ?, 'pending', ?)",
            (event_key, json.dumps([r["id"] for r in rules]), json.dumps(notification), at),
        ).rowcount
        if not claimed:
            return False
        for rule in rules:
            conn.execute(
                "UPDATE rules SET fired_at = ?, enabled = enabled AND mode != 'once' WHERE id = ?",
                (at, rule["id"]),
            )
    return True


def mark_sent(conn, event_key, ok):
    with conn:
        conn.execute("UPDATE sent SET status = ? WHERE event_key = ?", ("sent" if ok else "failed", event_key))


def unsent(conn, pending_before):
    """Notifications to retry: failed ones, and ones claimed before pending_before
    (an ISO time) but never sent, e.g. after a crash. Newer pending ones may still be in flight."""
    rows = conn.execute(
        "SELECT event_key, notification FROM sent"
        " WHERE status = 'failed' OR (status = 'pending' AND at < ?) ORDER BY at",
        (pending_before,),
    )
    return [(row["event_key"], json.loads(row["notification"])) for row in rows]


def reply_counts(conn, course_id):
    """{thread_id: (reply_count, seen_at)} for a course's known threads."""
    rows = conn.execute("SELECT thread_id, reply_count, seen_at FROM threads WHERE course_id = ?", (course_id,))
    return {row["thread_id"]: (row["reply_count"], row["seen_at"]) for row in rows}


def set_reply_counts(conn, course_id, counts, seen_at, keep_existing=False):
    """Store reply counts ({thread_id: count}) as of seen_at. With keep_existing,
    threads already known are left alone."""
    verb = "INSERT OR IGNORE" if keep_existing else "INSERT OR REPLACE"
    with conn:
        conn.executemany(
            f"{verb} INTO threads (thread_id, course_id, reply_count, seen_at) VALUES (?, ?, ?, ?)",
            [(thread_id, course_id, count, seen_at) for thread_id, count in counts.items()],
        )


def add_retry(conn, event_key, thread_id, comment_id, error):
    with conn:
        conn.execute(
            "INSERT INTO retries (event_key, thread_id, comment_id, error, attempts, first_failed_at)"
            " VALUES (?, ?, ?, ?, 1, ?)"
            " ON CONFLICT (event_key) DO UPDATE SET error = excluded.error, attempts = attempts + 1",
            (event_key, thread_id, comment_id, error, now()),
        )


def retries(conn):
    return [dict(row) for row in conn.execute("SELECT * FROM retries ORDER BY first_failed_at")]


def remove_retry(conn, event_key):
    with conn:
        conn.execute("DELETE FROM retries WHERE event_key = ?", (event_key,))
