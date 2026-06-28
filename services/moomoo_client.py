"""
moomoo_client.py — synchronous wrapper around the moomoo-api SDK for options
data. All moomoo SDK / pandas interaction is isolated here; callers receive
plain Python dicts so the rest of the pipeline stays SDK- and pandas-free.

The moomoo SDK is synchronous and talks to a local OpenD gateway over TCP
(see config.MOOMOO_HOST/PORT). OpenD must be running and logged in. Because
the SDK blocks, the cog calls these functions through asyncio.to_thread.

Field names and call signatures follow moomoo OpenAPI v10.x:
  - quote_ctx.get_option_expiration_date(code)      -> df['strike_time']
  - quote_ctx.get_option_chain(code, start, end, data_filter) -> df['code', ...]
  - quote_ctx.get_market_snapshot(code_list[<=400]) -> df with option_* cols
Returns follow the (ret, data) convention where ret == RET_OK on success and
data is a DataFrame, else an error string.

NOTE: not exercised on the dev machine (no OpenD here). The moomoo column
names are centralized in _normalize_option_row / _normalize_underlying_row so
any field-name drift across SDK versions is a one-place fix.
"""
import math
import socket
from datetime import date, timedelta

import config

# Imported lazily inside _quote_context so the rest of the bot loads even when
# moomoo-api isn't installed (e.g. the dev machine without OpenD).
_moomoo = None

# If OpenD isn't reachable, the SDK's OpenQuoteContext can block for a long
# time trying to connect rather than failing fast -- which hangs whatever
# command/loop is awaiting it. A cheap socket probe first turns that hang into
# an immediate, clear error.
_PORT_PROBE_TIMEOUT = 3.0


def _port_reachable() -> bool:
    try:
        with socket.create_connection((config.MOOMOO_HOST, config.MOOMOO_PORT), timeout=_PORT_PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def _load_moomoo():
    global _moomoo
    if _moomoo is None:
        import moomoo  # type: ignore
        _moomoo = moomoo
    return _moomoo


def _clean(value):
    """moomoo returns 'N/A' strings and NaN for absent numeric fields."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str) and value.strip() in ("", "N/A", "nan"):
        return None
    return value


def _to_float(value):
    value = _clean(value)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value):
    f = _to_float(value)
    return int(f) if f is not None else None


class _QuoteContext:
    """Context manager owning one OpenQuoteContext connection to OpenD."""

    def __init__(self):
        self._ctx = None

    def __enter__(self):
        mm = _load_moomoo()
        if not _port_reachable():
            raise RuntimeError(
                f"OpenD not reachable at {config.MOOMOO_HOST}:{config.MOOMOO_PORT} "
                f"(socket probe failed within {_PORT_PROBE_TIMEOUT}s). "
                "Is OpenD running and logged in? In Docker, set MOOMOO_HOST="
                "host.docker.internal and make OpenD listen on 0.0.0.0."
            )
        self._ctx = mm.OpenQuoteContext(host=config.MOOMOO_HOST, port=config.MOOMOO_PORT)
        return self._ctx

    def __exit__(self, *exc):
        if self._ctx is not None:
            self._ctx.close()


def check_health() -> tuple[bool, str]:
    """Return (ok, detail). Verifies OpenD is reachable and a trivial call works."""
    if not config.MOOMOO_ENABLED:
        return False, "MOOMOO_ENABLED is false"
    try:
        mm = _load_moomoo()
    except Exception as e:
        return False, f"moomoo-api not installed: {e}"
    if not _port_reachable():
        return False, (
            f"port {config.MOOMOO_HOST}:{config.MOOMOO_PORT} not reachable. "
            "OpenD down/not logged in, or (Docker) container can't reach the host "
            "— set MOOMOO_HOST=host.docker.internal and bind OpenD to 0.0.0.0."
        )
    try:
        with _QuoteContext() as ctx:
            ret, data = ctx.get_global_state()
            if ret != mm.RET_OK:
                return False, f"OpenD reachable but get_global_state failed: {data}"
            return True, "OpenD connected"
    except Exception as e:
        return False, f"cannot reach OpenD at {config.MOOMOO_HOST}:{config.MOOMOO_PORT}: {e}"


def _us_code(ticker: str) -> str:
    return f"US.{ticker.upper()}"


def build_universe(ticker: str) -> list[str]:
    """
    Contract-code universe for one ticker: near-dated, non-deep call+put
    contracts via get_option_chain with a server-side delta filter. Called
    once per day per ticker (chains barely change intraday); the returned
    codes are then snapshotted repeatedly through the session.
    Returns [] on any failure (caller skips the ticker this cycle).
    """
    from utils.constants import (
        OPTIONS_CHAIN_DTE_MAX,
        OPTIONS_CHAIN_DELTA_MIN,
        OPTIONS_CHAIN_DELTA_MAX,
    )

    mm = _load_moomoo()
    code = _us_code(ticker)
    codes: list[str] = []
    today = date.today()
    horizon = (today + timedelta(days=OPTIONS_CHAIN_DTE_MAX)).isoformat()

    with _QuoteContext() as ctx:
        ret, exp_df = ctx.get_option_expiration_date(code=code)
        if ret != mm.RET_OK:
            return []
        expiries = [str(d) for d in exp_df["strike_time"].values.tolist()]
        expiries = [d for d in expiries if d <= horizon]

        # Two delta filters: one for the positive (call) band, one for the
        # negative (put) band, since moomoo's delta filter is signed.
        call_filter = mm.OptionDataFilter()
        call_filter.delta_min = OPTIONS_CHAIN_DELTA_MIN
        call_filter.delta_max = OPTIONS_CHAIN_DELTA_MAX
        put_filter = mm.OptionDataFilter()
        put_filter.delta_min = -OPTIONS_CHAIN_DELTA_MAX
        put_filter.delta_max = -OPTIONS_CHAIN_DELTA_MIN

        for expiry in expiries:
            for data_filter in (call_filter, put_filter):
                ret, chain_df = ctx.get_option_chain(
                    code=code, start=expiry, end=expiry, data_filter=data_filter
                )
                if ret != mm.RET_OK:
                    continue
                codes.extend(str(c) for c in chain_df["code"].values.tolist())

    # Dedup while preserving order.
    seen = set()
    unique = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def _normalize_option_row(row: dict, ticker: str) -> dict | None:
    if not row.get("option_valid"):
        return None
    expiry = _clean(row.get("strike_time"))
    return {
        "contract_code": _clean(row.get("code")),
        "ticker": ticker,
        "option_type": (_clean(row.get("option_type")) or "").upper(),
        "strike": _to_float(row.get("option_strike_price")),
        "expiry": str(expiry) if expiry is not None else None,
        "dte": _to_int(row.get("option_expiry_date_distance")),
        "last_price": _to_float(row.get("last_price")),
        "volume": _to_int(row.get("volume")) or 0,
        "open_interest": _to_int(row.get("option_open_interest")),
        "iv": _to_float(row.get("option_implied_volatility")),
        "delta": _to_float(row.get("option_delta")),
        "multiplier": _to_int(row.get("option_contract_multiplier")) or 100,
    }


def fetch_ticker_snapshots(ticker: str, contract_codes: list[str]) -> tuple[float | None, list[dict]]:
    """
    Snapshot the underlying spot plus its option contracts. Options are
    requested in batches of <=400 (moomoo's per-request cap); the underlying
    is fetched in its own small call to keep the batching arithmetic trivial.
    Returns (underlying_price, [normalized option dicts]).
    """
    if not contract_codes:
        return None, []

    mm = _load_moomoo()
    underlying_price = None
    options: list[dict] = []
    batch_size = 400

    with _QuoteContext() as ctx:
        ret, df = ctx.get_market_snapshot([_us_code(ticker)])
        if ret == mm.RET_OK and len(df) > 0:
            underlying_price = _to_float(df.to_dict("records")[0].get("last_price"))

        for i in range(0, len(contract_codes), batch_size):
            batch = contract_codes[i:i + batch_size]
            ret, df = ctx.get_market_snapshot(batch)
            if ret != mm.RET_OK:
                continue
            for row in df.to_dict("records"):
                norm = _normalize_option_row(row, ticker)
                if norm and norm["contract_code"]:
                    options.append(norm)

    return underlying_price, options
