"""
Reason Engine — Step 13.
Every stock must explain WHY it passed or failed.
"""

from . import config


def generate_reasons(result: dict) -> list[str]:
    if result.get("error"):
        return [f"Error: {result['error']}"]

    reasons = []
    trend = result["trend"]

    # Step 2: Trend
    if trend["above_ema200"]:
        reasons.append("✓ Above EMA200")
    else:
        reasons.append("✗ Below EMA200")

    if trend["ema_aligned"]:
        reasons.append("✓ EMA50 > EMA200")
    else:
        reasons.append("✗ EMA50 <= EMA200")

    if trend["trend_quality"]:
        reasons.append("✓ EMA Slopes Positive")
    else:
        reasons.append("✗ EMA Slopes Flat/Negative")

    # Step 3: MACD
    if result["macd_bullish"]:
        reasons.append("✓ MACD Bullish")
    else:
        reasons.append("✗ MACD Bearish")

    # Step 4: RSI
    if not result["rsi_pass"]:
        reasons.append("✗ RSI below 50")
    elif result["rsi_extended"]:
        reasons.append(f"⚠ RSI Extended ({result['rsi']})")
    else:
        reasons.append(f"✓ RSI {result['rsi']}")

    # Step 5: Relative Volume
    if result["rvol_pass"]:
        reasons.append(f"✓ RVOL {result['rvol']}")
    else:
        reasons.append(f"✗ RVOL Low ({result['rvol']})")

    # Step 7: VPVR
    if result["vp"]["above_poc"]:
        reasons.append(f"✓ Above POC ({result['vp']['distance_from_poc_pct']}%)")
    else:
        reasons.append(f"✗ Below POC ({result['vp']['distance_from_poc_pct']}%)")

    # Step 8: Breakout
    if result["breakout_confirmed"]:
        reasons.append("✓ Breakout Confirmed")
    else:
        reasons.append("✗ Breakout Missing")

    # Step 9: Resistance
    if result["near_resistance"]:
        reasons.append("⚠ Near Resistance")

    return reasons


def get_next_action(decision: str) -> str:
    actions = {
        "READY": "Review Chart",
        "WATCH": "Wait for Breakout",
        "MONITOR": "Keep on Watchlist",
        "IGNORE": "No Action",
    }
    return actions.get(decision, "No Action")
