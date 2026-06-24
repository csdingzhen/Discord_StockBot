"""
jin10_store.py — SQLite persistence for the Jin10 flash-news pipeline.

One row per flash item, keyed by its `url` since Jin10's MCP API exposes no
separate id field (the url's path segment is itself a timestamp-derived,
unique, monotonically increasing identifier). Tracks classification
level/category plus whether an item has been posted as an L3 alert or rolled
into an L2 digest yet, so the polling loop and the digest-flush task can both
query this table without double-posting.
"""
from datetime import datetime, timezone

from storage.db import get_connection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jin10_flash (
    url TEXT PRIMARY KEY,
    time TEXT NOT NULL,
    content TEXT NOT NULL,
    level INTEGER,
    category TEXT,
    posted_l3 INTEGER NOT NULL DEFAULT 0,
    digested INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jin10_flash_pending_l2
    ON jin10_flash(level, digested);
"""


def init_db():
    conn = get_connection()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def is_seen(url: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute("SELECT 1 FROM jin10_flash WHERE url = ?", (url,)).fetchone()
        return row is not None
    finally:
        conn.close()


def save_classified_item(item: dict, level: int, category: str):
    """item: {'content', 'time', 'url'} as returned by Jin10MCPClient.list_flash()."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO jin10_flash (url, time, content, level, category, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                item["url"],
                item["time"],
                item["content"],
                level,
                category,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def mark_l3_posted(url: str):
    conn = get_connection()
    try:
        conn.execute("UPDATE jin10_flash SET posted_l3 = 1 WHERE url = ?", (url,))
        conn.commit()
    finally:
        conn.close()


def get_pending_l2_items() -> list[dict]:
    """Level-2 items not yet rolled into a digest, oldest first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT url, time, content, category FROM jin10_flash "
            "WHERE level = 2 AND digested = 0 ORDER BY time ASC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def mark_digested(urls: list[str]):
    if not urls:
        return
    conn = get_connection()
    try:
        conn.executemany(
            "UPDATE jin10_flash SET digested = 1 WHERE url = ?",
            [(url,) for url in urls],
        )
        conn.commit()
    finally:
        conn.close()
