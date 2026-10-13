"""Read-only Moomoo REST adapter. No OpenD, trading scopes, or SDK required.

Option-screen is aggregate activity, NOT an execution tape. See
deploy/options-intelligence.md for documented field and permission gaps.
"""
import asyncio
import base64
import hashlib
import json
import math
import re
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

import aiohttp
from yarl import URL

import config
from services.metrics import options_pipeline_total
from services.options_scan import excluded_underlying

HOST = "https://webapi.moomoo.com"
PREFIX = "/api/v1.0/quote/"


class MoomooRestError(RuntimeError):
    """Contains safe status information, never response bodies or credentials."""


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def sign_request(private_key, timestamp, method, path, query, body):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

    digest = hashlib.sha256(body).hexdigest() if body else ""
    message = "\n".join((str(timestamp), method, path, query, digest)).encode()
    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        signature = private_key.sign(message)
    elif isinstance(private_key, rsa.RSAPrivateKey):
        signature = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    else:
        raise MoomooRestError("Expected an Ed25519 or RSA private key")
    return base64.b64encode(signature).decode("ascii")


def retry_delay(value, fallback):
    seconds = number(value)
    if seconds is None and value:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            pass
    return max(0, seconds) if seconds is not None else fallback


def screen_payload(stock_ids=None, cursor=""):
    """None explicitly selects the market; an empty whitelist is never a scan."""
    if stock_ids is not None and not stock_ids:
        raise ValueError("An empty stock whitelist cannot select the whole market")
    # Numeric indicator IDs and units are documented by Moomoo's MCP tool schema.
    def interval(indicator, minimum, maximum=None):
        bounds = {"min_value": minimum}
        if maximum is not None:
            bounds["max_value"] = maximum
        return {"option_list": [{"indicator_type": indicator,
                                 "indicator_value": {"value_interval": bounds}}]}

    payload = {
        "strategy": {"market_category_list": [0], "filter_group_list": [
            interval(1002, 0, config.OPTIONS_MAX_DTE),
            interval(2011, config.OPTIONS_MIN_CONTRACT_VOLUME),
            interval(2012, int(config.OPTIONS_MIN_PREMIUM * 1000)),
        ]},
        "field_filter": {
            "hp_strike_price": 1, "option_type": 1, "left_day": 1,
            "price": 1, "volume": 1, "turnover": 1, "open_interest": 1,
            "implied_volatility": 1, "delta": 1, "option_name": "x",
            "bid_price": 1, "ask_price": 1, "multiplier": 1,
            "underlying_info": {"stock_id": 1, "price": 1, "iv_rank": 1},
        },
        "sort_obj": {"sort_field": {"volume": 1}},
        "limit": 1000, "next_key": cursor, "request_exact_data": 1,
    }
    # The public reference does not enumerate the complete OptionItem proto.
    # Permit an account-verified field mask without changing the strategy.
    if stock_ids is not None:
        payload["strategy"]["filter_group_list"].insert(0, {
            "underlying_list": [{"indicator_type": 101,
                                 "indicator_value": {"value_list": stock_ids}}]})
    if config.MOOMOO_SCREEN_FIELD_FILTER:
        payload["field_filter"] = config.MOOMOO_SCREEN_FIELD_FILTER
    return payload


def normalize_option(row, ticker, observed_at):
    code = row.get("code")
    if not isinstance(code, str) or not code.startswith("US."):
        return None
    expiry = row.get("strike_date")
    if expiry:
        try:
            expiry = datetime.strptime(str(expiry), "%Y%m%d").date().isoformat()
        except ValueError:
            expiry = None
    if not expiry:
        match = re.search(r"(\d{6})[CP]\d+$", code)
        if match:
            try:
                expiry = datetime.strptime(match[1], "%y%m%d").date().isoformat()
            except ValueError:
                pass
    underlying = row.get("underlying_info") or row.get("underlying") or {}
    return {
        "contract_code": code, "ticker": ticker, "expiry": expiry,
        "option_type": row.get("option_type"), "strike": number(row.get("strike_price")),
        "dte": number(row.get("left_day")), "last_price": number(row.get("price")),
        "volume": number(row.get("volume")), "open_interest": number(row.get("open_interest")),
        "turnover": number(row.get("turnover")), "iv": number(row.get("implied_volatility")),
        "delta": number(row.get("delta")), "multiplier": number(row.get("multiplier")),
        "bid": number(row.get("bid_price")), "ask": number(row.get("ask_price")),
        "underlying_price": number(underlying.get("price")),
        "underlying_iv_rank": number(underlying.get("iv_rank")),
        # The screen API does not promise an exchange quote timestamp or OI as-of date.
        "quote_time": None, "observed_at": observed_at,
        "source": "moomoo_rest", "execution_evidence": False,
    }


class MoomooRestClient:
    def __init__(self):
        self._key = None
        self._lock = asyncio.Lock()
        self._next_request = 0.0
        self._profiles = {}

    def _credentials(self):
        if not config.MOOMOO_APP_KEY or not config.MOOMOO_PRIVATE_KEY_PEM:
            raise MoomooRestError("Configure MOOMOO_APP_KEY and MOOMOO_PRIVATE_KEY_PEM")
        if self._key is None:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            try:
                self._key = load_pem_private_key(
                    config.MOOMOO_PRIVATE_KEY_PEM.replace("\\n", "\n").encode(), password=None)
            except (TypeError, ValueError):
                raise MoomooRestError("Invalid private-key PEM") from None

    async def request(self, endpoint, payload=None, params=None):
        if endpoint not in ("stock-basicinfo", "option-screen", "find-news"):
            raise MoomooRestError("Endpoint not in read-only allowlist")
        self._credentials()
        method = "POST" if payload is not None else "GET"
        path = PREFIX + endpoint
        query = urlencode(params or {})
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else b""
        url = URL(HOST + path + ("?" + query if query else ""), encoded=True)
        async with self._lock:
            for attempt in range(3):
                delay = max(0, self._next_request - time.monotonic())
                if delay > 30:
                    raise MoomooRestError("Moomoo rate-limit cooldown active")
                await asyncio.sleep(delay)
                timestamp = int(time.time() * 1000)
                headers = {
                    "X-Api-Key": config.MOOMOO_APP_KEY, "X-Timestamp": str(timestamp),
                    "X-Nonce": uuid.uuid4().hex, "Content-Type": "application/json",
                    "Authorization": sign_request(self._key, timestamp, method, path, query, body),
                }
                self._next_request = time.monotonic() + config.MOOMOO_REQUEST_INTERVAL
                try:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                        async with session.request(method, url, headers=headers, data=body or None,
                                                   allow_redirects=False) as response:
                            options_pipeline_total.labels(stage="moomoo_request", status=str(response.status)).inc()
                            if response.status == 429:
                                wait = retry_delay(response.headers.get("Retry-After"), 2 ** (attempt + 1))
                                self._next_request = time.monotonic() + max(wait, config.MOOMOO_REQUEST_INTERVAL)
                                if attempt < 2 and wait <= 30:
                                    continue
                            if response.status != 200:
                                raise MoomooRestError(f"Moomoo HTTP {response.status}")
                            result = await response.json(content_type=None)
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                    raise MoomooRestError("Moomoo transport/JSON failure") from None
                if not isinstance(result, dict) or result.get("ret_code") != 0:
                    code = result.get("ret_code") if isinstance(result, dict) else None
                    safe_code = code if isinstance(code, int) else "unknown"
                    raise MoomooRestError(f"Moomoo business error {safe_code}; check permissions/schema")
                return result

    async def profiles(self, tickers):
        missing = sorted(set(tickers) - self._profiles.keys())
        for start in range(0, len(missing), 400):
            result = await self.request("stock-basicinfo", {"code_list": [
                "US." + t for t in missing[start:start + 400]]})
            for row in result.get("data", {}).get("basic_list", []):
                ticker = str(row.get("code", "")).removeprefix("US.")
                if ticker in missing and isinstance(row.get("stock_id"), int):
                    self._profiles[ticker] = row
        unresolved = sorted(set(tickers) - self._profiles.keys())
        if unresolved:
            raise MoomooRestError("Incomplete underlying metadata; no unrestricted fallback scan")
        return {t: self._profiles[t] for t in tickers}

    async def scan(self, tickers=None):
        """Market-wide when None; explicit lists remain fail-closed lookups."""
        if tickers is not None:
            tickers = sorted({t for t in tickers if not excluded_underlying(t)})
        if tickers == []:
            return [], {"complete": True, "profiles": {}}
        started = time.monotonic()
        profiles = await self.profiles(tickers) if tickers is not None else {}
        stock_ids = [p["stock_id"] for p in profiles.values()] if tickers is not None else None
        owners = {str(p["stock_id"]): t for t, p in profiles.items()}
        snapshots, seen, cursors = [], set(), set()
        coverage = {"complete": False, "profiles": profiles, "scope": "market" if tickers is None else "watchlist",
                    "pages": 0, "rows_received": 0, "excluded": 0, "unresolved": 0,
                    "duplicates": 0, "total": None, "stop_reason": "page_cap"}
        cursor = ""
        for _ in range(config.MOOMOO_MAX_PAGES):
            remaining = config.MOOMOO_SCAN_TIMEOUT_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                if not coverage["pages"]:
                    raise MoomooRestError("Options scan deadline exceeded before first page")
                coverage["stop_reason"] = "timeout"
                break
            try:
                result = await asyncio.wait_for(self.request("option-screen", screen_payload(stock_ids, cursor)), remaining)
            except asyncio.TimeoutError:
                if not coverage["pages"]:
                    raise MoomooRestError("Options scan deadline exceeded before first page") from None
                coverage["stop_reason"] = "timeout"
                break
            rows = result.get("data", {}).get("option_list")
            if not isinstance(rows, list):
                raise MoomooRestError("Missing option_list in screen response")
            coverage["pages"] += 1
            coverage["rows_received"] += len(rows)
            observed_at = datetime.now(timezone.utc).isoformat()
            for row in rows:
                if not isinstance(row, dict):
                    raise MoomooRestError("Invalid option row in screen response")
                owner = row.get("underlying_info") or row.get("underlying") or {}
                ticker = owners.get(str(owner.get("stock_id")))
                match = re.fullmatch(r"US\.([A-Z.][A-Z0-9.]*)\d{6}[CP]\d+", str(row.get("code", "")))
                root = match[1] if match else ""
                if excluded_underlying(ticker or root) or excluded_underlying(root):
                    coverage["excluded"] += 1
                    continue
                if ticker is None:
                    if tickers is None:
                        # Standard US equity roots only. Adjusted numeric roots need
                        # an authoritative owner mapping, never strip-and-guess.
                        ticker = root if re.fullmatch(r"[A-Z]+(?:\.[A-Z]+)?", root) else None
                    else:
                        ticker = root if root in profiles else None
                snap = normalize_option(row, ticker, observed_at) if ticker else None
                if snap is None:
                    coverage["unresolved"] += 1
                elif snap["contract_code"] in seen:
                    coverage["duplicates"] += 1
                else:
                    seen.add(snap["contract_code"])
                    snapshots.append(snap)
            page = result.get("pagination") or {}
            coverage["total"] = page.get("total")
            if not isinstance(page.get("has_more"), bool):
                raise MoomooRestError("Missing pagination completeness in screen response")
            if not page.get("has_more"):
                coverage["complete"] = coverage["unresolved"] == 0
                coverage["stop_reason"] = "unresolved" if coverage["unresolved"] else "exhausted"
                break
            cursor = page.get("next_key")
            if not cursor or cursor == "-1" or cursor in cursors:
                raise MoomooRestError("Invalid or repeated screen cursor")
            cursors.add(cursor)
        self._validate_fields(snapshots)
        coverage["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return snapshots, coverage

    @staticmethod
    def _validate_fields(snapshots):
        if snapshots and not any(s["volume"] is not None and s["dte"] is not None
                and s["option_type"] in ("CALL", "PUT")
                and (s["turnover"] is not None or s["last_price"] is not None and s["multiplier"] is not None)
                for s in snapshots):
            raise MoomooRestError("Screen response lacks scoring fields; verify OptionItem field_filter")

    async def health(self):
        await self.profiles(["AAPL"])
        profile = self._profiles["AAPL"]
        probe = screen_payload([profile["stock_id"]])
        probe["limit"] = 1
        result = await self.request("option-screen", probe)
        rows = result.get("data", {}).get("option_list")
        if not isinstance(rows, list):
            raise MoomooRestError("Missing option_list in health response")
        normalized = [s for row in rows
            if (s := normalize_option(row, "AAPL", datetime.now(timezone.utc).isoformat()))]
        self._validate_fields(normalized)
        missing = [k for k in ("volume", "open_interest", "turnover", "iv", "bid", "ask")
                   if not any(s.get(k) is not None for s in normalized)]
        detail = "REST authentication and option-screen accepted; execution tape not verified."
        if not rows:
            return detail + " Empty screen: field availability and entitlements still need verification."
        return detail + (" Missing sample fields: " + ", ".join(missing) if missing else " Core sample fields present.")
