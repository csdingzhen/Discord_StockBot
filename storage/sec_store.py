"""
sec_store.py — SQLite persistence for the SEC EDGAR filing monitor.

One row per filing, keyed by its accession number (SEC's own unique id for
every filing, e.g. "0000320193-26-000011"). Tracks whether an EX-99.1
earnings-release analysis has been generated yet, so the polling loop and the
after-market push can both query this table without double-analyzing or
double-posting.
"""
from datetime import datetime, timezone

from storage.db import get_connection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sec_filings (
    accession_number TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    cik TEXT NOT NULL,
    form_type TEXT NOT NULL,
    items TEXT,
    filing_date TEXT NOT NULL,
    analysis_json TEXT,
    analyzed INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sec_filings_ticker_date
    ON sec_filings(ticker, filing_date);
"""


def init_db():
    conn = get_connection()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def is_seen(accession_number: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM sec_filings WHERE accession_number = ?", (accession_number,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def save_filing(filing: dict):
    """filing: {'accession_number', 'ticker', 'cik', 'form_type', 'items', 'filing_date'}."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO sec_filings
                (accession_number, ticker, cik, form_type, items, filing_date, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filing["accession_number"],
                filing["ticker"],
                filing["cik"],
                filing["form_type"],
                filing.get("items"),
                filing["filing_date"],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def save_analysis(accession_number: str, analysis_json: str):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sec_filings SET analysis_json = ?, analyzed = 1 WHERE accession_number = ?",
            (analysis_json, accession_number),
        )
        conn.commit()
    finally:
        conn.close()


def get_analyzed_filing_for_ticker_date(ticker: str, filing_date: str) -> dict | None:
    """The analyzed EX-99.1 filing for a ticker on a given date (YYYY-MM-DD), if any."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM sec_filings WHERE ticker = ? AND filing_date = ? "
            "AND analyzed = 1 ORDER BY fetched_at DESC LIMIT 1",
            (ticker, filing_date),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
