"""
sec_filings.py — SEC EDGAR filing monitor.

Detects new earnings-related filings (8-K Item 2.02, 10-Q, 10-K, 6-K) for
watchlist tickers via SEC's free, unauthenticated submissions API, and
extracts the EX-99.1 press-release exhibit text from 8-K filings for LLM
analysis. 10-Q/10-K/6-K filings are detected and stored but not text-analyzed
in v1 — their bodies are dozens of pages of XBRL-tagged legal boilerplate and
need real section-extraction to be worth feeding to an LLM, whereas the
EX-99.1 exhibit is a clean, short, plain-prose press release that's already
the de facto earnings announcement.

SEC requires a descriptive User-Agent on every request and a 10 req/sec rate
limit across all of www.sec.gov / data.sec.gov — see config.SEC_EDGAR_USER_AGENT.
No API key needed.
"""
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

import config
from storage import sec_store

_HEADERS = {"User-Agent": config.SEC_EDGAR_USER_AGENT}
_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

_EARNINGS_8K_ITEM = "2.02"
_OTHER_EARNINGS_FORMS = {"10-Q", "10-K", "6-K"}

_EX991_PATTERN = re.compile(r"ex.?99\.?1", re.IGNORECASE)

_cik_map_cache: dict[str, str] = {}
_cik_map_fetched_at: datetime | None = None
_CIK_MAP_TTL = timedelta(hours=24)


def _load_cik_map() -> dict[str, str]:
    """Ticker -> zero-padded 10-digit CIK string. Refreshed at most once per day."""
    global _cik_map_cache, _cik_map_fetched_at
    now = datetime.now(timezone.utc)
    if _cik_map_cache and _cik_map_fetched_at and now - _cik_map_fetched_at < _CIK_MAP_TTL:
        return _cik_map_cache

    try:
        r = requests.get(_TICKER_MAP_URL, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        # company_tickers.json is a dict keyed by stringified index, not a list
        new_map = {entry["ticker"].upper(): str(entry["cik_str"]).zfill(10) for entry in data.values()}
        if new_map:
            _cik_map_cache = new_map
            _cik_map_fetched_at = now
    except Exception:
        pass  # fall back to whatever's already cached, possibly stale

    return _cik_map_cache


def get_cik_for_ticker(ticker: str) -> str | None:
    return _load_cik_map().get(ticker.upper())


def _is_earnings_filing(form_type: str, items: str) -> bool:
    if form_type == "8-K":
        item_list = (items or "").split(",")
        return _EARNINGS_8K_ITEM in item_list
    return form_type in _OTHER_EARNINGS_FORMS


def fetch_new_earnings_filings(watchlist: set, lookback_days: int = 2) -> list[dict]:
    """
    Poll each watchlist ticker's submissions feed for newly filed earnings-
    related filings not already in the store. Returns newly-seen filings
    (already persisted via sec_store.save_filing) as a list of dicts:
    {ticker, cik, accession_number, form_type, items, filing_date, primary_document}.
    """
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    new_filings = []

    for ticker in sorted(watchlist):
        cik = get_cik_for_ticker(ticker)
        if not cik:
            continue
        try:
            r = requests.get(
                _SUBMISSIONS_URL.format(cik=cik), headers=_HEADERS, timeout=15
            )
            r.raise_for_status()
            recent = r.json()["filings"]["recent"]
        except Exception:
            time.sleep(0.15)
            continue

        forms = recent.get("form", [])
        items_list = recent.get("items", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        docs = recent.get("primaryDocument", [])

        for i in range(len(forms)):
            filing_date = dates[i] if i < len(dates) else ""
            if filing_date < cutoff:
                continue
            form_type = forms[i]
            items = items_list[i] if i < len(items_list) else ""
            if not _is_earnings_filing(form_type, items):
                continue
            accession = accessions[i]
            if sec_store.is_seen(accession):
                continue

            filing = {
                "ticker": ticker,
                "cik": cik,
                "accession_number": accession,
                "form_type": form_type,
                "items": items,
                "filing_date": filing_date,
                "primary_document": docs[i] if i < len(docs) else "",
            }
            sec_store.save_filing(filing)
            new_filings.append(filing)

        time.sleep(0.15)  # stay well under the 10 req/sec rate limit

    return new_filings


def fetch_ex99_text(cik: str, accession_number: str, primary_document: str) -> str | None:
    """
    For an 8-K filing, locate and extract plain text from its EX-99.1 press-
    release exhibit. Falls back to the primary document if no EX-99.1-named
    file is found in the filing's directory. Returns None on any failure.
    """
    accession_nodash = accession_number.replace("-", "")
    cik_int = str(int(cik))  # archive paths use the un-padded CIK
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/index.json"

    try:
        r = requests.get(index_url, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        items = r.json().get("directory", {}).get("item", [])
    except Exception:
        items = []

    target_doc = primary_document
    for item in items:
        name = item.get("name", "")
        if _EX991_PATTERN.search(name):
            target_doc = name
            break

    if not target_doc:
        return None

    doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{target_doc}"
    try:
        r = requests.get(doc_url, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(separator="\n", strip=True)
        return text or None
    except Exception:
        return None
