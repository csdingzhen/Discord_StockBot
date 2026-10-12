"""Deterministic activity scoring. Snapshot activity is not buy-side flow."""
import math
import re
from datetime import datetime, timezone

import config


def excluded_underlying(ticker):
    # Strip namespace/index punctuation and adjusted-option suffixes only.
    symbol = str(ticker).upper().removeprefix("US.").lstrip(".^$")
    return (symbol in config.OPTIONS_EXCLUDED_UNDERLYINGS
            or re.sub(r"\d+$", "", symbol) in config.OPTIONS_EXCLUDED_UNDERLYINGS)


def score_weights():
    defaults = {"vol_gt_oi": 25, "premium_activity": 20, "iv_jump": 20,
                "relative_volume": 20, "short_dte": 10, "interval_activity": 5,
                "aggressive_buying": 10, "repeated_buying": 10}
    overrides = config.OPTIONS_SCORE_WEIGHTS
    if not isinstance(overrides, dict) or set(overrides) - defaults.keys():
        raise ValueError("Unknown options score weight")
    for key, value in overrides.items():
        if finite(value) is None or not 0 <= float(value) <= 100:
            raise ValueError("Options weights must be finite numbers from 0 to 100")
        defaults[key] = float(value)
    return defaults


def finite(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def utc(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (TypeError, ValueError):
        return None


def execution_features(trades, option_type, as_of):
    """Optional tape contract; Moomoo US screens do not supply these trades.

    Quotes must precede executions by at most one second. Even aligned
    quote classification is inference, not confirmed opening buyer activity.
    """
    rows, seen = [], set()
    for trade in trades:
        stamp, key = utc(trade.get("timestamp")), trade.get("id")
        price, size, mult = (finite(trade.get(k)) for k in ("price", "size", "multiplier"))
        if (not isinstance(key, (str, int)) or key in seen or stamp is None
                or not 0 <= (as_of - stamp).total_seconds() <= config.OPTIONS_EXECUTION_WINDOW_SECONDS
                or any(v is None or v <= 0 for v in (price, size, mult))
                or trade.get("cancelled") or trade.get("late")):
            continue
        seen.add(key)
        side, confidence = "unknown", 0.0
        bid, ask = finite(trade.get("bid")), finite(trade.get("ask"))
        quote_time = utc(trade.get("quote_time"))
        aligned = quote_time and 0 <= (stamp - quote_time).total_seconds() <= 1
        if aligned and bid is not None and ask is not None and 0 < bid < ask:
            if (ask - bid) / ((ask + bid) / 2) <= config.OPTIONS_MAX_SPREAD_RATIO:
                if price >= ask - (ask - bid) * 0.1:
                    side, confidence = "buy", 0.8
                elif price <= bid + (ask - bid) * 0.1:
                    side, confidence = "sell", 0.8
        vendor = str(trade.get("direction", "")).lower()
        if side == "unknown" and vendor in ("buy", "sell"):
            side, confidence = vendor, 0.4
        elif vendor in ("buy", "sell") and vendor != side:
            side, confidence = "unknown", 0.0
        rows.append({**trade, "side": side, "confidence": confidence,
                     "premium": price * size * mult, "timestamp": stamp.isoformat()})
    total = sum(t["premium"] for t in rows)
    buy = sum(t["premium"] for t in rows if t["side"] == "buy")
    sell = sum(t["premium"] for t in rows if t["side"] == "sell")
    confidence = sum(t["confidence"] * t["premium"] for t in rows) / total if total else 0
    bias = "unknown"
    if total and (buy + sell) / total >= 0.5 and option_type in ("CALL", "PUT"):
        if abs(buy - sell) / max(buy + sell, 1) < 0.5:
            bias = "mixed"
        else:
            bias = "bullish" if (buy > sell) == (option_type == "CALL") else "bearish"
    aggressive = [t for t in rows if t["side"] == "buy" and t["confidence"] >= 0.8]
    return {"directional_bias": bias, "direction_confidence": round(confidence * 100),
            "aggressive_buying": "quote_inference" if aggressive else "unknown",
            "aggressive_premium": sum(t["premium"] for t in aggressive),
            "execution_premium": total or None, "repeated_buying": len(aggressive) >= config.OPTIONS_REPEAT_COUNT,
            "meaningful_execution": any(t["premium"] >= config.OPTIONS_MIN_PREMIUM for t in rows)
                or sum(t["premium"] for t in aggressive) >= config.OPTIONS_MIN_PREMIUM,
            "trades": rows}


def score_contract(snap, prior_iv=None, vol_stats=None, *, as_of=None, previous=None):
    if excluded_underlying(snap.get("ticker")):
        return None
    as_of = as_of or datetime.now(timezone.utc)
    volume, dte = finite(snap.get("volume")), finite(snap.get("dte"))
    if (volume is None or volume < config.OPTIONS_MIN_CONTRACT_VOLUME or
            dte is None or not 0 <= dte <= config.OPTIONS_MAX_DTE
            or snap.get("option_type") not in ("CALL", "PUT")):
        return None
    stamp = utc(snap.get("quote_time"))
    if stamp and not 0 <= (as_of - stamp).total_seconds() <= config.OPTIONS_MAX_QUOTE_AGE_SECONDS:
        return None
    bid, ask = finite(snap.get("bid")), finite(snap.get("ask"))
    spread = None
    if bid is not None and ask is not None:
        if bid <= 0 or ask <= bid:
            return None
        spread = (ask - bid) / ((ask + bid) / 2)
        if spread > config.OPTIONS_MAX_SPREAD_RATIO:
            return None
    turnover, basis = finite(snap.get("turnover")), "reported_daily_turnover"
    if turnover is None:
        price, mult = finite(snap.get("last_price")), finite(snap.get("multiplier"))
        if price is None or mult is None or min(price, mult) <= 0:
            return None
        turnover, basis = volume * price * mult, "estimated_daily_premium"
    if turnover < config.OPTIONS_MIN_PREMIUM:
        return None
    oi = finite(snap.get("open_interest"))
    if oi is not None and oi < 0:
        oi = None
    ratio = volume / oi if oi and oi > 0 else None
    points = {}
    if oi is not None and volume > oi:
        # Zero OI does not prove opening trades or infinite unusualness.
        points["vol_gt_oi"] = 10 if not oi else min(25, round(10 + 10 * math.log2(ratio)))
    points["premium_activity"] = min(20, max(0, round(5 * math.log2(
        max(1, turnover / max(config.OPTIONS_MIN_PREMIUM, 1))))))
    iv, prior_iv = finite(snap.get("iv")), finite(prior_iv)
    iv_jump = iv - prior_iv if iv is not None and prior_iv is not None else None
    if iv_jump is not None and iv_jump >= config.OPTIONS_MIN_IV_JUMP:
        points["iv_jump"] = min(20, round(iv_jump * 2))
    rel_volume = z = None
    if vol_stats and vol_stats[2] >= config.OPTIONS_MIN_HISTORY_DAYS:
        mean, std, _ = vol_stats
        if mean > 0:
            rel_volume = volume / mean
            if rel_volume >= config.OPTIONS_MIN_RELATIVE_VOLUME:
                points["relative_volume"] = min(20, round(rel_volume * 5))
        if std > 0:
            z = (volume - mean) / std
    spot, strike = finite(snap.get("underlying_price")), finite(snap.get("strike"))
    otm = None
    if spot and spot > 0 and strike and strike > 0 and snap.get("option_type") in ("CALL", "PUT"):
        otm = strike > spot if snap["option_type"] == "CALL" else strike < spot
    if dte <= config.OPTIONS_SHORT_DTE:
        points["short_dte"] = 5 + (5 if otm else 0)
    interval_volume = interval_turnover = None
    if previous:
        then, before = utc(previous.get("observed_at")), finite(previous.get("volume"))
        if then and 0 < (as_of - then).total_seconds() <= config.OPTIONS_SCAN_MINUTES * 180:
            if before is not None and volume >= before:
                interval_volume = volume - before
                if interval_volume >= config.OPTIONS_MIN_CONTRACT_VOLUME:
                    points["interval_activity"] = 5
            previous_turnover = finite(previous.get("turnover"))
            if previous_turnover is not None and snap.get("turnover") is not None and turnover >= previous_turnover:
                interval_turnover = turnover - previous_turnover
    execution = execution_features(snap.get("trades", []) if snap.get("execution_evidence") else [],
                                   snap.get("option_type"), as_of)
    if execution["aggressive_premium"] >= config.OPTIONS_MIN_PREMIUM and execution["aggressive_buying"] == "quote_inference":
        points["aggressive_buying"] = 10
        if execution["repeated_buying"]:
            points["repeated_buying"] = 10
    weights = score_weights()
    reference = {"vol_gt_oi": 25, "premium_activity": 20, "iv_jump": 20,
                 "relative_volume": 20, "short_dte": 10, "interval_activity": 5,
                 "aggressive_buying": 10, "repeated_buying": 10}
    points = {key: round(value * weights[key] / reference[key]) for key, value in points.items()}
    score = max(0, min(100, sum(points.values())))
    tier = 3 if score >= config.OPTIONS_L3_SCORE else 2 if score >= config.OPTIONS_L2_SCORE else 1
    return {**snap, **execution, "score": score, "tier": tier, "points": points,
            "reasons": [k for k, v in points.items() if v], "notional": turnover,
            "premium_basis": basis, "vol_oi_ratio": ratio, "iv_jump": iv_jump,
            "vol_zscore": z, "relative_volume": rel_volume, "otm": otm,
            "spread_ratio": spread, "quote_age_known": stamp is not None,
            "multi_leg_risk": "unknown", "interval_volume": interval_volume,
            "interval_turnover": interval_turnover,
            "history_days": vol_stats[2] if vol_stats else 0}


def detect_anomalies(snapshots, prior_iv_map, vol_baseline=None, max_l3_per_ticker=3,
                     *, as_of=None, previous=None):
    """Compatible entry point; delivery limits apply to ticker events."""
    results = []
    for snap in snapshots:
        code = snap.get("contract_code")
        result = score_contract(snap, prior_iv_map.get(code), (vol_baseline or {}).get(code),
                                as_of=as_of, previous=(previous or {}).get(code))
        if result and result["tier"] >= 2:
            results.append(result)
    return sorted(results, key=lambda a: (a["score"], a["notional"]), reverse=True)


def aggregate_events(anomalies, as_of):
    groups = {}
    for anomaly in anomalies:
        groups.setdefault(anomaly["ticker"], {})[anomaly["contract_code"]] = anomaly
    events = []
    for ticker, contracts in groups.items():
        rows = sorted(contracts.values(), key=lambda a: (a["score"], a["notional"]), reverse=True)
        biases = {r["directional_bias"] for r in rows if r["directional_bias"] != "unknown"}
        trades = [(r["contract_code"], t) for r in rows for t in r["trades"]]
        likely_multi = any(
            lc != rc and left.get("size") == right.get("size")
            and abs((utc(left["timestamp"]) - utc(right["timestamp"])).total_seconds()) <= 1
            for i, (lc, left) in enumerate(trades) for rc, right in trades[i + 1:])
        total = sum(r["notional"] for r in rows)
        expiry_totals, strike_totals = {}, {}
        for row in rows:
            expiry = row.get("expiry") or "unknown"
            strike = str(row.get("strike"))
            expiry_totals[expiry] = expiry_totals.get(expiry, 0) + row["notional"]
            strike_totals[strike] = strike_totals.get(strike, 0) + row["notional"]
        # Do not reward apparent directional execution features on likely spreads.
        scores = [min(100, sum(v for k, v in r["points"].items() if k not in ("aggressive_buying", "repeated_buying")))
                  if likely_multi else r["score"] for r in rows]
        score = max(scores)
        if score < config.OPTIONS_L2_SCORE:
            continue
        events.append({"ticker": ticker, "as_of": as_of.isoformat(), "contracts": rows,
                       "score": score, "tier": 3 if score >= config.OPTIONS_L3_SCORE else 2,
                       "premium": total, "premium_basis": "selected_contracts_daily_activity",
                       "expiry_premium": expiry_totals, "strike_premium": strike_totals,
                       "top_contract_share": max(r["notional"] for r in rows) / total if total else None,
                       "directional_bias": "unknown" if likely_multi else
                           next(iter(biases)) if len(biases) == 1 else "mixed" if biases else "unknown",
                       "direction_confidence": 0 if likely_multi else round(sum(
                           r["direction_confidence"] * r["notional"] for r in rows) / total),
                       "multi_leg_risk": "likely" if likely_multi else "unknown"})
    return sorted(events, key=lambda e: (e["score"], e["premium"]), reverse=True)


def contract_label(anomaly):
    strike = finite(anomaly.get("strike"))
    strike_str = f"{strike:g}" if strike is not None else "?"
    cp = {"CALL": "C", "PUT": "P"}.get(anomaly.get("option_type"), "?")
    return f"{anomaly.get('ticker')} {strike_str}{cp} {anomaly.get('expiry') or ''}".strip()


def reason_labels(reasons):
    labels = {"vol_gt_oi": "Volume > reported OI", "premium_activity": "Premium activity",
              "iv_jump": "IV expansion", "relative_volume": "Relative volume",
              "short_dte": "Short DTE / OTM", "interval_activity": "Interval volume growth",
              "aggressive_buying": "Quote-inferred buying", "repeated_buying": "Repeated inferred buys"}
    return [labels.get(reason, reason) for reason in reasons]
