"""
options_store.py — SQLite persistence for the options-flow pipeline.

Two tables:
  option_snapshots — one row per contract per scan, the raw metric history.
                     Used for the IV-jump baseline (prior day's IV) and, once
                     enough days accumulate, future volume/IV z-scoring.
  option_alerts    — one row per (contract, day) that crossed alerting tier.
                     Dedups same-contract re-alerts within a day and tracks
                     which tier-2 items still need rolling into a digest.
"""
from datetime import datetime, timezone

from storage.db import get_connection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS option_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_code TEXT NOT NULL,
    ticker TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    last_price REAL,
    volume INTEGER,
    open_interest INTEGER,
    iv REAL,
    delta REAL,
    strike REAL,
    expiry TEXT,
    option_type TEXT,
    underlying_price REAL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_option_snap_code_date
    ON option_snapshots(contract_code, snapshot_date);

CREATE TABLE IF NOT EXISTS option_alerts (
    contract_code TEXT NOT NULL,
    alert_date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    tier INTEGER NOT NULL,
    notional REAL,
    signals_json TEXT,
    digested INTEGER NOT NULL DEFAULT 0,
    posted_at TEXT NOT NULL,
    PRIMARY KEY (contract_code, alert_date)
);
"""


def init_db():
    conn = get_connection()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def save_snapshots(snapshots: list[dict], snapshot_date: str, underlying_price: float | None):
    if not snapshots:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.executemany(
            """
            INSERT INTO option_snapshots
                (contract_code, ticker, snapshot_date, last_price, volume,
                 open_interest, iv, delta, strike, expiry, option_type,
                 underlying_price, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    s.get("contract_code"), s.get("ticker"), snapshot_date,
                    s.get("last_price"), s.get("volume"), s.get("open_interest"),
                    s.get("iv"), s.get("delta"), s.get("strike"), s.get("expiry"),
                    s.get("option_type"), underlying_price, now,
                )
                for s in snapshots
            ],
        )
        conn.commit()
    finally:
        conn.close()


def get_prior_iv_map(ticker: str, before_date: str) -> dict[str, float]:
    """Most recent IV per contract recorded *before* before_date (i.e. the
    prior trading day's reading), for IV-jump detection. Empty on cold start."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT contract_code, iv
            FROM option_snapshots
            WHERE ticker = ? AND snapshot_date < ? AND iv IS NOT NULL
            GROUP BY contract_code
            HAVING snapshot_date = MAX(snapshot_date)
            """,
            (ticker, before_date),
        ).fetchall()
        return {r["contract_code"]: r["iv"] for r in rows}
    finally:
        conn.close()


def record_alert(anomaly: dict, alert_date: str, signals_json: str) -> bool:
    """Insert an alert row. Returns True if newly inserted, False if this
    contract was already alerted today (dedup) -- callers post only on True."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO option_alerts
                (contract_code, alert_date, ticker, tier, notional, signals_json, digested, posted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                anomaly.get("contract_code"), alert_date, anomaly.get("ticker"),
                anomaly.get("tier"), anomaly.get("notional"), signals_json,
                0 if anomaly.get("tier") == 2 else 1,  # tier-2 awaits digest; tier-3 posted now
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_pending_digest(alert_date: str) -> list[dict]:
    """Tier-2 alerts not yet rolled into a digest."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT contract_code, ticker, tier, notional, signals_json "
            "FROM option_alerts WHERE alert_date = ? AND tier = 2 AND digested = 0 "
            "ORDER BY notional DESC",
            (alert_date,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_digested(contract_codes: list[str], alert_date: str):
    if not contract_codes:
        return
    conn = get_connection()
    try:
        conn.executemany(
            "UPDATE option_alerts SET digested = 1 WHERE contract_code = ? AND alert_date = ?",
            [(c, alert_date) for c in contract_codes],
        )
        conn.commit()
    finally:
        conn.close()
