"""
yf_session.py — one shared HTTP session for all yfinance calls.

Why this exists: yfinance authenticates by grabbing a throwaway cookie from
https://fc.yahoo.com, whose TLS certificate intentionally does not match that
hostname. yfinance's curl_cffi backend enforces certificate verification and
*raises* CertificateVerifyError on it (rather than returning None), so
yfinance's own basic->csrf cookie fallback never runs and every .info /
.history call dies with:

    curl: (60) SSL: no alternative certificate subject name matches target
    hostname 'fc.yahoo.com'

This path only fetches *public* market data — no credentials of ours are sent —
so we hand yfinance a curl_cffi session with certificate verification relaxed
and a real browser fingerprint (so Yahoo doesn't bot-block us). Pass SESSION to
every yf.Ticker(...) so they all share one cookie/crumb and this config.
"""
from curl_cffi import requests as _curl_requests

SESSION = _curl_requests.Session(impersonate="chrome", verify=False)
