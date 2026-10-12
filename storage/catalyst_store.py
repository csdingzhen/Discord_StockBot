"""Point-in-time local evidence, persistent research cache and usage reservations."""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import config
from services.options_scan import utc
from storage.db import get_connection


def init_db():
    conn = get_connection()
    try:
        conn.executescript("""
          CREATE TABLE IF NOT EXISTS catalyst_calendar (
            ticker TEXT NOT NULL, event_date TEXT NOT NULL, observed_at TEXT NOT NULL,
            payload_json TEXT NOT NULL, PRIMARY KEY(ticker, event_date, observed_at)
          );
          CREATE INDEX IF NOT EXISTS idx_catalyst_calendar ON catalyst_calendar(ticker, observed_at);
          CREATE TABLE IF NOT EXISTS catalyst_cache (
            cache_key TEXT PRIMARY KEY, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS catalyst_calls (
            call_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, ticker TEXT NOT NULL,
            stage TEXT NOT NULL, model TEXT, status TEXT NOT NULL, reserved_usd REAL NOT NULL,
            cost_usd REAL, prompt_tokens INTEGER, completion_tokens INTEGER, latency_seconds REAL
          );
          CREATE INDEX IF NOT EXISTS idx_catalyst_calls_date ON catalyst_calls(created_at);
        """)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(catalyst_calls)")}
        for column in ("event_id", "usage_json"):
            if column not in columns:
                conn.execute(f"ALTER TABLE catalyst_calls ADD COLUMN {column} TEXT")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "jin10_flash" in tables:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jin10_fetched ON jin10_flash(fetched_at)")
        conn.commit()
    finally:
        conn.close()


def cache_earnings(entries):
    """Best-effort reuse of already fetched calendars; no additional API calls."""
    if not entries:
        return
    init_db()
    observed = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.executemany("INSERT OR IGNORE INTO catalyst_calendar VALUES (?, ?, ?, ?)",
                         [(e["symbol"], e["date"], observed, json.dumps(e, allow_nan=False))
                          for e in entries if e.get("symbol") and e.get("date")])
        conn.commit()
    finally:
        conn.close()


def safe_url(value):
    if not isinstance(value, str) or len(value) > 1500 or any(c in value for c in "\n\r<>()"):
        return None
    try:
        parts = urlsplit(value)
        return value if parts.scheme in ("http", "https") and parts.hostname and not parts.username else None
    except ValueError:
        return None


def local_evidence(ticker, aliases, as_of):
    cutoff = as_of - timedelta(days=config.CATALYST_LOOKBACK_DAYS)
    terms = [ticker, *aliases]
    pattern = re.compile("|".join(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])"
                                  if t.isascii() else re.escape(t) for t in terms if t), re.I)
    conn = get_connection()
    evidence = []
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "jin10_flash" in tables:
            rows = conn.execute("""SELECT * FROM jin10_flash WHERE fetched_at>=? AND fetched_at<=?
                ORDER BY fetched_at DESC LIMIT 2000""", (cutoff.isoformat(), as_of.isoformat())).fetchall()
            for row in rows:
                if not pattern.search(row["content"]):
                    continue
                published = utc(row["time"])
                if published is None:
                    try:
                        # Jin10's naive wall-clock text is interpreted as Beijing time.
                        published = datetime.fromisoformat(row["time"]).replace(
                            tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)
                    except ValueError:
                        continue
                if cutoff <= published <= as_of and safe_url(row["url"]):
                    evidence.append({"source": "jin10", "url": row["url"],
                                     "published_at": published.isoformat(), "observed_at": row["fetched_at"],
                                     "text": row["content"][:900], "primary": False})
        if "sec_filings" in tables:
            rows = conn.execute("""SELECT * FROM sec_filings WHERE ticker=? AND filing_date>=?
                AND filing_date<=? AND fetched_at<=? ORDER BY filing_date DESC LIMIT 8""",
                (ticker, cutoff.date().isoformat(), as_of.date().isoformat(), as_of.isoformat())).fetchall()
            for row in rows:
                accession = row["accession_number"]
                if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) or not row["cik"].isdigit():
                    continue
                url = (f"https://www.sec.gov/Archives/edgar/data/{int(row['cik'])}/"
                       f"{accession.replace('-', '')}/{accession}-index.html")
                evidence.append({"source": "sec", "url": url, "published_at": None,
                    "publication_date": row["filing_date"], "observed_at": row["fetched_at"],
                    "primary": True, "text": f"{ticker} filed {row['form_type']}; items {row['items']}. "
                    "Filing existence only; no financial contents verified in this evidence."})
                analyzed_at = utc(row["analyzed_at"]) if "analyzed_at" in row.keys() else None
                if analyzed_at and analyzed_at <= as_of and row["analysis_json"]:
                    evidence[-1]["text"] += " Previously generated AI summary (not a primary-source quote): " + row["analysis_json"][:650]
                    evidence[-1]["summary_observed_at"] = analyzed_at.isoformat()
        rows = conn.execute("""SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER(PARTITION BY event_date ORDER BY observed_at DESC) AS rank
            FROM catalyst_calendar WHERE ticker=? AND observed_at<=? AND observed_at>=?
          ) WHERE rank=1 ORDER BY event_date LIMIT 8""",
          (ticker, as_of.isoformat(), cutoff.isoformat())).fetchall()
        for row in rows:
            evidence.append({"source": "earnings_calendar", "url": "https://www.nasdaq.com/market-activity/earnings",
                "published_at": None, "observed_at": row["observed_at"], "event_date": row["event_date"],
                "primary": False, "text": "FMP/Nasdaq merged calendar, dates may be estimates: " + row["payload_json"][:700]})
    finally:
        conn.close()
    evidence.sort(key=lambda e: (e["primary"], e.get("published_at") or e["observed_at"]), reverse=True)
    return evidence[:10]


def get_cached(key, as_of):
    conn = get_connection()
    try:
        row = conn.execute("SELECT payload_json FROM catalyst_cache WHERE cache_key=? AND created_at<=? AND expires_at>?",
                           (key, as_of.isoformat(), as_of.isoformat())).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


def put_cached(key, payload, created_at):
    expires = created_at + timedelta(minutes=config.CATALYST_CACHE_MINUTES)
    conn = get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO catalyst_cache VALUES (?, ?, ?, ?)",
                     (key, created_at.isoformat(), expires.isoformat(), json.dumps(payload, allow_nan=False)))
        conn.commit()
    finally:
        conn.close()


def reserve_call(ticker, stage, model, event_id=None):
    """Atomic persistent limits; unknown charges keep their reservation on failure."""
    now = datetime.now(timezone.utc)
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""SELECT COUNT(*) AS n, COALESCE(SUM(COALESCE(cost_usd, reserved_usd)),0) AS cost
            FROM catalyst_calls WHERE created_at>=?""", (now.date().isoformat(),)).fetchone()
        if (row["n"] >= config.CATALYST_DAILY_REQUEST_LIMIT or
                row["cost"] + config.CATALYST_CALL_RESERVE_USD > config.CATALYST_DAILY_BUDGET_USD):
            return None
        call_id = uuid.uuid4().hex
        conn.execute("""INSERT INTO catalyst_calls
            (call_id, created_at, ticker, stage, model, status, reserved_usd, event_id) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (call_id, now.isoformat(), ticker, stage, model, config.CATALYST_CALL_RESERVE_USD, event_id))
        conn.commit()
        return call_id
    finally:
        conn.close()


def finish_call(call_id, status, metadata, latency):
    from services.options_scan import finite
    usage = metadata.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    cost = finite(usage.get("cost"))
    conn = get_connection()
    try:
        conn.execute("""UPDATE catalyst_calls SET status=?, cost_usd=?, prompt_tokens=?,
            completion_tokens=?, latency_seconds=?, usage_json=? WHERE call_id=?""",
            (status, cost if cost is not None and cost >= 0 else None,
             usage.get("prompt_tokens"), usage.get("completion_tokens"), latency,
             json.dumps(usage, allow_nan=False), call_id))
        conn.commit()
    finally:
        conn.close()
