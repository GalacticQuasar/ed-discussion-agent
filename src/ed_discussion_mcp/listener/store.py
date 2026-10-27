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
CREATE TABLE IF NOT EXISTS judgments (event_key TEXT NOT NULL, rule_id INTEGER NOT NULL, score REAL NOT NULL, model TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sent (event_key TEXT PRIMARY KEY, rule_ids TEXT NOT NULL, status TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
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
    return conn


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
