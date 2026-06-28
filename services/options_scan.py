"""
options_scan.py — pure options-anomaly scoring/tiering. No SDK, no Discord,
no DB; takes normalized snapshot dicts (from moomoo_client) plus baselines
(prior-day IV, trailing volume stats) and returns scored anomalies. Kept
dependency-free so the scoring rules are unit-testable in isolation.

Detection model (selective, "balanced"):
  - Noise floor: volume and notional must clear minimums to be considered.
  - Necessary gate: volume > open interest (more traded today than open).
  - Corroborating signals: strong vol/OI, large notional, IV jump, near-money
    + near-dated, or a volume z-score outlier vs the contract's own history.
  - L3 (immediate alert): gate AND >=1 corroborating signal, OR an extreme
    standalone (notional >= EXTREME, or z >= EXTREME).
  - L2 (digest): gate alone, or a single corroborating signal without the gate.
  - Per-ticker L3 cap: only the top few L3 per name survive; the rest demote
    to L2 so one liquid name can't flood the channel.

"notional" is volume x price x multiplier -- a proxy for premium traded, NOT
real block/sweep tape (moomoo's quote API exposes no trade-level prints).
"""
from utils.constants import (
    OPTIONS_MIN_VOLUME,
    OPTIONS_MIN_NOTIONAL,
    OPTIONS_VOL_OI_GATE,
    OPTIONS_VOL_OI_STRONG,
    OPTIONS_NOTIONAL_STRONG,
    OPTIONS_NOTIONAL_EXTREME,
    OPTIONS_IV_JUMP,
    OPTIONS_NEAR_DTE,
    OPTIONS_NEAR_MONEY_DELTA,
    OPTIONS_VOL_Z,
    OPTIONS_VOL_Z_EXTREME,
    OPTIONS_BASELINE_MIN_DAYS,
    OPTIONS_MAX_L3_PER_TICKER,
)


def _notional(snap: dict) -> float | None:
    price = snap.get("last_price")
    vol = snap.get("volume")
    mult = snap.get("multiplier") or 100
    if price is None or not vol:
        return None
    return price * vol * mult


def _volume_zscore(volume: int, vol_stats: tuple | None) -> float | None:
    """vol_stats = (mean, std, n) of the contract's prior daily volumes."""
    if not vol_stats:
        return None
    mean, std, n = vol_stats
    if n < OPTIONS_BASELINE_MIN_DAYS or not std or std <= 0:
        return None
    return (volume - mean) / std


def score_contract(snap: dict, prior_iv: float | None, vol_stats: tuple | None = None) -> dict | None:
    """
    Score one option snapshot. Returns a signals dict (incl. computed tier and
    which conditions fired), or None if below the activity noise floor.
    """
    volume = snap.get("volume") or 0
    if volume < OPTIONS_MIN_VOLUME:
        return None

    notional = _notional(snap)
    if notional is None or notional < OPTIONS_MIN_NOTIONAL:
        return None

    oi = snap.get("open_interest")
    vol_oi = (volume / oi) if oi else None
    # Gate: more contracts traded today than were open. oi None = missing data
    # (don't assume); oi 0 with real volume = brand-new positioning = gate true.
    gate = (oi is not None) and (volume > oi)

    iv = snap.get("iv")
    iv_jump = (iv - prior_iv) if (iv is not None and prior_iv is not None) else None
    vol_z = _volume_zscore(volume, vol_stats)

    dte = snap.get("dte")
    delta = snap.get("delta")
    abs_delta = abs(delta) if delta is not None else None
    near_dated = dte is not None and dte <= OPTIONS_NEAR_DTE
    lo, hi = OPTIONS_NEAR_MONEY_DELTA
    near_money = abs_delta is not None and lo <= abs_delta <= hi

    corroborating = []
    if vol_oi is not None and vol_oi >= OPTIONS_VOL_OI_STRONG:
        corroborating.append("vol_oi_strong")
    if notional >= OPTIONS_NOTIONAL_STRONG:
        corroborating.append("notional")
    if iv_jump is not None and iv_jump >= OPTIONS_IV_JUMP:
        corroborating.append("iv_jump")
    if near_dated and near_money:
        corroborating.append("near_money_dated")
    if vol_z is not None and vol_z >= OPTIONS_VOL_Z:
        corroborating.append("vol_zscore")

    extreme = notional >= OPTIONS_NOTIONAL_EXTREME or (vol_z is not None and vol_z >= OPTIONS_VOL_Z_EXTREME)

    if extreme or (gate and corroborating):
        tier = 3
    elif gate or corroborating:
        tier = 2
    else:
        tier = 1

    # Display reasons: lead with the gate when it fired.
    reasons = (["vol_gt_oi"] if gate else []) + corroborating

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
        "vol_zscore": vol_z,
        "notional": notional,
        "reasons": reasons,
        "tier": tier,
    }


def _rank_key(r: dict):
    return (r["tier"], r.get("vol_zscore") or 0.0, r["notional"])


def detect_anomalies(
    snapshots: list[dict],
    prior_iv_map: dict[str, float],
    vol_baseline: dict[str, tuple] | None = None,
    max_l3_per_ticker: int = OPTIONS_MAX_L3_PER_TICKER,
) -> list[dict]:
    """
    Score every snapshot; return tier-2/3 results sorted most-significant
    first. Enforces a per-ticker cap on L3 -- beyond the cap, the least
    significant L3s demote to L2 (still surfaced, via the digest).
    """
    vol_baseline = vol_baseline or {}
    scored = []
    for snap in snapshots:
        code = snap.get("contract_code")
        result = score_contract(snap, prior_iv_map.get(code), vol_baseline.get(code))
        if result and result["tier"] >= 2:
            scored.append(result)
    scored.sort(key=_rank_key, reverse=True)

    l3_seen: dict[str, int] = {}
    for r in scored:
        if r["tier"] == 3:
            t = r["ticker"]
            l3_seen[t] = l3_seen.get(t, 0) + 1
            if l3_seen[t] > max_l3_per_ticker:
                r["tier"] = 2  # demote overflow to the digest
    scored.sort(key=_rank_key, reverse=True)
    return scored


def contract_label(anomaly: dict) -> str:
    """Human-readable contract id, e.g. 'AAPL 250C 2026-07-18'."""
    strike = anomaly.get("strike")
    strike_str = f"{strike:g}" if strike is not None else "?"
    cp = "C" if anomaly.get("option_type") == "CALL" else "P"
    return f"{anomaly.get('ticker')} {strike_str}{cp} {anomaly.get('expiry') or ''}".strip()


_REASON_LABELS = {
    "vol_gt_oi": "成交量超过持仓量",
    "vol_oi_strong": "量仓比≥3",
    "notional": "大额名义成交",
    "iv_jump": "隐含波动率跳升",
    "near_money_dated": "近月平值定向押注",
    "vol_zscore": "成交量异常(z≥2)",
}


def reason_labels(reasons: list[str]) -> list[str]:
    return [_REASON_LABELS.get(r, r) for r in reasons]
