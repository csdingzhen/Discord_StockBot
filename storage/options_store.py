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
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

import config
from storage.db import get_connection
from utils.constants import OPTIONS_BASELINE_LOOKBACK_DAYS
from utils.market_time import ET
from services.options_scan import excluded_underlying

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
        columns = {r[1] for r in conn.execute("PRAGMA table_info(option_snapshots)")}
        for name, kind in (("metrics_json", "TEXT"), ("market_minute", "INTEGER"), ("source", "TEXT")):
            if name not in columns:
                conn.execute(f"ALTER TABLE option_snapshots ADD COLUMN {name} {kind}")
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_option_context
              ON option_snapshots(ticker, source, snapshot_date, market_minute);
            CREATE TABLE IF NOT EXISTS option_events (
              event_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, as_of TEXT NOT NULL,
              tier INTEGER NOT NULL, score INTEGER NOT NULL, payload_json TEXT NOT NULL,
              delivery TEXT NOT NULL DEFAULT 'pending', sent_at TEXT,
              catalyst_json TEXT, outcomes_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_option_events_delivery ON option_events(delivery, as_of);
            CREATE INDEX IF NOT EXISTS idx_option_events_ticker ON option_events(ticker, as_of);
        """)
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
                 underlying_price, fetched_at, metrics_json, market_minute, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    s.get("contract_code"), s.get("ticker"), snapshot_date,
                    s.get("last_price"), s.get("volume"), s.get("open_interest"),
                    s.get("iv"), s.get("delta"), s.get("strike"), s.get("expiry"),
                    s.get("option_type"), underlying_price, s.get("observed_at") or now,
                    json.dumps(s, allow_nan=False),
                    _market_minute(s.get("observed_at") or now), s.get("source", "opend"),
                )
                for s in snapshots
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _market_minute(stamp):
    dt = datetime.fromisoformat(stamp).astimezone(ET)
    return dt.hour * 60 + dt.minute


def get_scoring_context(ticker, as_of, source):
    """Same-clock baselines; absent screened contracts are missing, not zero."""
    day = as_of.astimezone(ET).date().isoformat()
    cutoff = (as_of.astimezone(ET).date() - timedelta(days=30)).isoformat()
    minute = _market_minute(as_of.isoformat())
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM (
              SELECT contract_code, snapshot_date, volume, iv, market_minute, metrics_json,
                ROW_NUMBER() OVER (PARTITION BY contract_code, snapshot_date
                                   ORDER BY market_minute DESC, fetched_at DESC, id DESC) AS rank
              FROM option_snapshots WHERE ticker=? AND source=? AND snapshot_date>=?
                AND snapshot_date<=? AND fetched_at<=? AND market_minute<=?
            ) WHERE rank=1
        """, (ticker, source, cutoff, day, as_of.isoformat(), minute)).fetchall()
    finally:
        conn.close()
    volumes, ivs, previous = {}, {}, {}
    for row in rows:
        code = row["contract_code"]
        if row["snapshot_date"] == day:
            previous[code] = json.loads(row["metrics_json"] or "{}")
        elif minute - row["market_minute"] <= max(10, config.OPTIONS_SCAN_MINUTES * 2):
            if row["volume"] is not None:
                volumes.setdefault(code, []).append(row["volume"])
            if row["iv"] is not None and row["snapshot_date"] > ivs.get(code, ("", None))[0]:
                ivs[code] = (row["snapshot_date"], row["iv"])
    stats = {}
    for code, values in volumes.items():
        n, mean = len(values), sum(values) / len(values)
        std = (sum((v - mean) ** 2 for v in values) / (n - 1)) ** 0.5 if n > 1 else 0
        stats[code] = (mean, std, n)
    return {code: value[1] for code, value in ivs.items()}, stats, previous


def save_event(event):
    """Dedup ongoing ticker events; do not mark delivered before Discord accepts."""
    as_of = datetime.fromisoformat(event["as_of"])
    day_start = as_of.astimezone(ET).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("""SELECT * FROM option_events WHERE ticker=? AND as_of>=? AND as_of<=?
            ORDER BY as_of DESC LIMIT 1""", (event["ticker"], day_start.isoformat(), event["as_of"])).fetchone()
        if previous:
            old = json.loads(previous["payload_json"])
            anchor = previous["sent_at"] or previous["as_of"]
            in_cooldown = (as_of - datetime.fromisoformat(anchor)).total_seconds() < config.OPTIONS_EVENT_COOLDOWN_MINUTES * 60
            old_volumes = {r["contract_code"]: r["volume"] for r in old["contracts"]}
            new_volume = sum(max(0, r["volume"] - old_volumes.get(r["contract_code"], 0)) for r in event["contracts"])
            changed = new_volume >= config.OPTIONS_MIN_CONTRACT_VOLUME or event["score"] >= old["score"] + 10
            if previous["delivery"] == "pending" or in_cooldown or not changed:
                event_id = previous["event_id"]
                event["event_id"] = event_id
                # Keep the strongest pending observation, and its original as-of.
                if previous["delivery"] == "pending":
                    chosen = event if event["score"] >= old["score"] else old
                    conn.execute("""UPDATE option_events SET as_of=?, tier=?, score=?, payload_json=? WHERE event_id=?""",
                        (chosen["as_of"], chosen["tier"], chosen["score"], json.dumps(chosen, allow_nan=False), event_id))
                conn.commit()
                return event_id
        event_id = hashlib.sha256(f"{event['ticker']}:{event['as_of']}".encode()).hexdigest()[:24]
        event["event_id"] = event_id
        conn.execute("""INSERT INTO option_events
            (event_id, ticker, as_of, tier, score, payload_json) VALUES (?, ?, ?, ?, ?, ?)""",
            (event_id, event["ticker"], event["as_of"], event["tier"], event["score"],
             json.dumps(event, allow_nan=False)))
        conn.commit()
    finally:
        conn.close()
    return event_id


def prune_snapshots(as_of):
    cutoff = (as_of.astimezone(ET).date() - timedelta(days=config.OPTIONS_RETAIN_SNAPSHOTS_DAYS)).isoformat()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM option_snapshots WHERE snapshot_date<?", (cutoff,))
        conn.commit()
    finally:
        conn.close()


def pending_events(as_of, tier=None):
    day_start = as_of.astimezone(ET).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    conn = get_connection()
    try:
        # Old queued events must obey a newly deployed exclusion policy too.
        excluded = [r[0] for r in conn.execute(
            "SELECT DISTINCT ticker FROM option_events WHERE delivery='pending'")
            if excluded_underlying(r[0])]
        conn.executemany("UPDATE option_events SET delivery='excluded' WHERE delivery='pending' AND ticker=?",
                         [(ticker,) for ticker in excluded])
        conn.commit()
        rows = conn.execute("""SELECT payload_json FROM option_events
            WHERE delivery='pending' AND as_of>=? AND as_of<=? AND (? IS NULL OR tier=?)
            ORDER BY score DESC, as_of LIMIT 100""", (day_start.isoformat(), as_of.isoformat(), tier, tier)).fetchall()
        return [json.loads(r[0]) for r in rows]
    finally:
        conn.close()


def complete_event(event_id):
    conn = get_connection()
    try:
        conn.execute("UPDATE option_events SET delivery='sent', sent_at=? WHERE event_id=?",
                     (datetime.now(timezone.utc).isoformat(), event_id))
        conn.commit()
    finally:
        conn.close()


def save_event_research(event_id, result):
    conn = get_connection()
    try:
        conn.execute("UPDATE option_events SET catalyst_json=? WHERE event_id=?",
                     (json.dumps(result, allow_nan=False), event_id))
        conn.commit()
    finally:
        conn.close()


def record_outcome(event_id, observed_at, values):
    """Outcome import hook: caller supplies later observations, never predictions."""
    observed = datetime.fromisoformat(observed_at)
    if observed.tzinfo is None or observed > datetime.now(timezone.utc):
        raise ValueError("Outcome must be an observed, aware timestamp, not a future prediction")
    conn = get_connection()
    try:
        row = conn.execute("SELECT as_of, outcomes_json FROM option_events WHERE event_id=?", (event_id,)).fetchone()
        if not row or observed <= datetime.fromisoformat(row["as_of"]):
            raise ValueError("Outcome must be after an existing anomaly")
        observations = json.loads(row["outcomes_json"] or "[]")
        observations.append({"observed_at": observed_at, "values": values})
        conn.execute("UPDATE option_events SET outcomes_json=? WHERE event_id=?",
                     (json.dumps(observations, allow_nan=False), event_id))
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


def get_volume_baseline(
    ticker: str, before_date: str, lookback_days: int = OPTIONS_BASELINE_LOOKBACK_DAYS
) -> dict[str, tuple]:
    """
    Per-contract trailing daily-volume stats for the z-score, over the
    lookback window of prior days (strictly before before_date). Each day's
    volume is that day's peak snapshot (cumulative daily volume). Returns
    {contract_code: (mean, std, n_days)}; empty during cold start.
    """
    start = (date.fromisoformat(before_date) - timedelta(days=lookback_days)).isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT contract_code, snapshot_date, MAX(volume) AS day_vol
            FROM option_snapshots
            WHERE ticker = ? AND snapshot_date < ? AND snapshot_date >= ?
            GROUP BY contract_code, snapshot_date
            """,
            (ticker, before_date, start),
        ).fetchall()
    finally:
        conn.close()

    series: dict[str, list[float]] = {}
    for r in rows:
        if r["day_vol"] is not None:
            series.setdefault(r["contract_code"], []).append(float(r["day_vol"]))

    out: dict[str, tuple] = {}
    for code, vols in series.items():
        n = len(vols)
        mean = sum(vols) / n
        if n >= 2:
            var = sum((v - mean) ** 2 for v in vols) / (n - 1)
            std = var ** 0.5
        else:
            std = 0.0
        out[code] = (mean, std, n)
    return out


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
