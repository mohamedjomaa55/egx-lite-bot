"""
Filters — Market Filter (Step 1) and Trend Filter (Step 2).
"""

from . import config
from .data_provider import fetch_history
from .indicators import ema, ema_slope

# Major EGX stocks used as a market proxy when index is unavailable
MARKET_PROXY_TICKERS = ["COMI", "HRHO", "EAST", "SWDY", "ORAS", "EFIH", "ETEL", "JUFO"]


def market_filter() -> dict:
    """
    Step 1 — Market Filter.
    Analyze EGX30 index (or composite of major stocks) to determine market status.
    Rules:
        Index > EMA200         → bullish point
        EMA50 > EMA200         → bullish point
        EMA200 Slope > 0       → bullish point
    """
    try:
        data = fetch_history(config.EGX_INDEX)
        if data.empty:
            raise ValueError("Empty index data")
        tickers_used = [config.EGX_INDEX]
    except Exception:
        # Fallback: use composite of major stocks
        data = _build_composite(MARKET_PROXY_TICKERS[:5])
        tickers_used = MARKET_PROXY_TICKERS[:5]
        if data is None or data.empty:
            return {"status": "NEUTRAL", "warning": "Could not determine market status", "tickers_used": []}

    if len(data) < config.EMA_SLOW + 10:
        return {"status": "NEUTRAL", "warning": "Insufficient data for market analysis", "tickers_used": tickers_used}

    close = data["Close"].astype(float)
    last_close = float(close.iloc[-1])
    last_ema50 = float(ema(close, config.EMA_MID).iloc[-1])
    last_ema200 = float(ema(close, config.EMA_SLOW).iloc[-1])
    ema200_slope = float(ema_slope(ema(close, config.EMA_SLOW), lookback=5).iloc[-1])

    bullish_count = 0
    if last_close > last_ema200:
        bullish_count += 1
    if last_ema50 > last_ema200:
        bullish_count += 1
    if ema200_slope > 0:
        bullish_count += 1

    if bullish_count >= 3:
        status = "BULLISH"
    elif bullish_count >= 2:
        status = "NEUTRAL"
    else:
        status = "BEARISH"

    warning = ""
    if status == "BEARISH":
        warning = "Weak Market — Signals should be treated cautiously."
    elif status == "NEUTRAL":
        warning = "Neutral Market — Proceed with caution."

    return {
        "status": status,
        "warning": warning,
        "index_close": round(last_close, 2),
        "ema50": round(last_ema50, 2),
        "ema200": round(last_ema200, 2),
        "ema200_slope": round(ema200_slope, 4),
        "tickers_used": tickers_used,
    }


def _build_composite(tickers: list[str]):
    """Build a price-weighted composite from multiple EGX stocks."""
    import pandas as pd
    frames = []
    for t in tickers:
        try:
            d = fetch_history(t)
            if not d.empty and len(d) > config.EMA_SLOW + 10:
                frames.append(d["Close"].astype(float))
        except Exception:
            continue
    if not frames:
        return None
    composite = pd.concat(frames, axis=1).mean(axis=1)
    result = pd.DataFrame({"Close": composite, "High": composite, "Low": composite, "Volume": 0})
    return result


def trend_filter(close, ema50_series, ema200_series) -> dict:
    """
    Step 2 — Trend Filter.
    Reject if Close < EMA200.
    Reject if EMA50 <= EMA200.
    Trend Quality: +5 bonus if both slopes positive.
    """
    last_close = float(close.iloc[-1])
    last_ema50 = float(ema50_series.iloc[-1])
    last_ema200 = float(ema200_series.iloc[-1])

    above_ema200 = last_close > last_ema200
    ema_aligned = last_ema50 > last_ema200

    passes = above_ema200 and ema_aligned

    # Trend Quality — slope bonus
    ema50_slope = float(ema_slope(ema50_series, lookback=5).iloc[-1])
    ema200_slope = float(ema_slope(ema200_series, lookback=5).iloc[-1])
    trend_quality = ema50_slope > 0 and ema200_slope > 0

    return {
        "passes": passes,
        "above_ema200": above_ema200,
        "ema_aligned": ema_aligned,
        "trend_quality": trend_quality,
        "ema50_slope": round(ema50_slope, 4),
        "ema200_slope": round(ema200_slope, 4),
    }
