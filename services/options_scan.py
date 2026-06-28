"""
options_scan.py — pure options-anomaly scoring/tiering. No SDK, no Discord,
no DB; takes normalized snapshot dicts (from moomoo_client) plus a prior-IV
baseline and returns scored anomalies. Kept dependency-free so the scoring
rules are unit-testable in isolation.

Heuristic, honestly framed: "notional" is volume x price x multiplier, a
proxy for premium traded -- NOT real block/sweep tape (moomoo's quote API
exposes no trade-level prints). Tiering counts how many independent "strong"
conditions a contract trips.
"""
from utils.constants import (
    OPTIONS_MIN_VOLUME,
    OPTIONS_MIN_NOTIONAL,
    OPTIONS_VOL_OI_RATIO,
    OPTIONS_NOTIONAL_STRONG,
    OPTIONS_NOTIONAL_EXTREME,
    OPTIONS_IV_JUMP,
    OPTIONS_NEAR_DTE,
    OPTIONS_NEAR_MONEY_DELTA,
    OPTIONS_TIER3_MIN_SIGNALS,
    OPTIONS_TIER2_MIN_SIGNALS,
)


def _notional(snap: dict) -> float | None:
    price = snap.get("last_price")
    vol = snap.get("volume")
    mult = snap.get("multiplier") or 100
    if price is None or not vol:
        return None
    return price * vol * mult


def score_contract(snap: dict, prior_iv: float | None) -> dict | None:
    """
    Score one option snapshot. Returns a signals dict augmented with the
    computed tier and a list of which strong conditions fired, or None if the
    contract is below the activity noise floor (not worth recording at all).
    """
    volume = snap.get("volume") or 0
    if volume < OPTIONS_MIN_VOLUME:
        return None

    notional = _notional(snap)
    if notional is None or notional < OPTIONS_MIN_NOTIONAL:
        return None

    oi = snap.get("open_interest")
    vol_oi = (volume / oi) if oi else None
    iv = snap.get("iv")
    iv_jump = (iv - prior_iv) if (iv is not None and prior_iv is not None) else None
    dte = snap.get("dte")
    delta = snap.get("delta")
    abs_delta = abs(delta) if delta is not None else None

    near_dated = dte is not None and dte <= OPTIONS_NEAR_DTE
    lo, hi = OPTIONS_NEAR_MONEY_DELTA
    near_money = abs_delta is not None and lo <= abs_delta <= hi

    reasons = []
    if vol_oi is not None and vol_oi >= OPTIONS_VOL_OI_RATIO:
        reasons.append("vol_oi")
    if notional >= OPTIONS_NOTIONAL_STRONG:
        reasons.append("notional")
    if iv_jump is not None and iv_jump >= OPTIONS_IV_JUMP:
        reasons.append("iv_jump")
    if near_dated and near_money:
        reasons.append("near_money_dated")

    strong_count = len(reasons)
    if notional >= OPTIONS_NOTIONAL_EXTREME or strong_count >= OPTIONS_TIER3_MIN_SIGNALS:
        tier = 3
    elif strong_count >= OPTIONS_TIER2_MIN_SIGNALS:
        tier = 2
    else:
        tier = 1

    return {
        "contract_code": snap.get("contract_code"),
        "ticker": snap.get("ticker"),
        "option_type": snap.get("option_type"),
        "strike": snap.get("strike"),
        "expiry": snap.get("expiry"),
        "dte": dte,
        "last_price": snap.get("last_price"),
        "volume": volume,
        "open_interest": oi,
        "iv": iv,
        "iv_jump": iv_jump,
        "delta": delta,
        "vol_oi_ratio": vol_oi,
        "notional": notional,
        "reasons": reasons,
        "tier": tier,
    }


def detect_anomalies(snapshots: list[dict], prior_iv_map: dict[str, float]) -> list[dict]:
    """
    Score every snapshot, returning only tier-2 and tier-3 results (tier-1 is
    below alerting interest), sorted by tier desc then notional desc so the
    most significant flow surfaces first.
    """
    scored = []
    for snap in snapshots:
        result = score_contract(snap, prior_iv_map.get(snap.get("contract_code")))
        if result and result["tier"] >= 2:
            scored.append(result)
    scored.sort(key=lambda r: (r["tier"], r["notional"]), reverse=True)
    return scored


def contract_label(anomaly: dict) -> str:
    """Human-readable contract id, e.g. 'AAPL 250.0C 2026-07-18'."""
    strike = anomaly.get("strike")
    strike_str = f"{strike:g}" if strike is not None else "?"
    cp = "C" if anomaly.get("option_type") == "CALL" else "P"
    return f"{anomaly.get('ticker')} {strike_str}{cp} {anomaly.get('expiry') or ''}".strip()


_REASON_LABELS = {
    "vol_oi": "成交量/持仓量异常",
    "notional": "大额名义成交",
    "iv_jump": "隐含波动率跳升",
    "near_money_dated": "近月平值定向押注",
}


def reason_labels(reasons: list[str]) -> list[str]:
    return [_REASON_LABELS.get(r, r) for r in reasons]
