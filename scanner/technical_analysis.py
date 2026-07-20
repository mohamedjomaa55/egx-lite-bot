"""
Full technical analysis for a single stock.
Steps 2–10 of the specification.
"""

import pandas as pd
from . import config
from .indicators import ema, rsi, macd, atr, find_resistance
from .volume_profile import build_volume_profile
from .data_provider import fetch_history
from .filters import trend_filter


def analyze(ticker: str) -> dict:
    """Analyze a single stock. Returns all raw data needed for scoring."""
    data = fetch_history(ticker)
    if data.empty or len(data) < config.EMA_SLOW + 10:
        return {"ticker": ticker, "error": "Insufficient data"}

    close = data["Close"].astype(float)
    high = data["High"].astype(float)
    low = data["Low"].astype(float)
    volume = data["Volume"].astype(float)

    # ── Step 2: EMA Indicators ────────────────────────────────────────
    ema200_series = ema(close, config.EMA_SLOW)
    ema50_series = ema(close, config.EMA_MID)
    ema20_series = ema(close, config.EMA_FAST)

    last_close = round(float(close.iloc[-1]), 2)
    last_ema20 = round(float(ema20_series.iloc[-1]), 2)
    last_ema50 = round(float(ema50_series.iloc[-1]), 2)
    last_ema200 = round(float(ema200_series.iloc[-1]), 2)

    trend = trend_filter(close, ema50_series, ema200_series)

    # ── Step 3: Momentum (MACD) ───────────────────────────────────────
    macd_line, macd_signal, macd_hist = macd(
        close, config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL
    )
    last_macd = round(float(macd_line.iloc[-1]), 4)
    last_macd_signal = round(float(macd_signal.iloc[-1]), 4)
    last_macd_hist = round(float(macd_hist.iloc[-1]), 4)
    macd_bullish = last_macd > last_macd_signal and last_macd_hist >= 0

    # ── Step 4: RSI ───────────────────────────────────────────────────
    rsi_val = rsi(close, config.RSI_PERIOD)
    last_rsi = round(float(rsi_val.iloc[-1]), 2)

    rsi_extended = False
    rsi_warning = ""
    if last_rsi < 50:
        rsi_pass = False
    else:
        rsi_pass = True
        if last_rsi > config.RSI_EXTENDED_THRESHOLD:
            rsi_extended = True
            rsi_warning = "Extended"

    # ── Step 5: Relative Volume ───────────────────────────────────────
    last_volume = int(volume.iloc[-1])
    avg_vol = float(volume.iloc[-config.RVOL_MA_PERIOD:].mean())
    rvol = round(last_volume / avg_vol, 2) if avg_vol > 0 else 0
    rvol_pass = rvol >= config.RVOL_MIN_THRESHOLD

    # ── Step 6: Liquidity ─────────────────────────────────────────────
    avg_daily_value = round(float((close * volume).iloc[-20:].mean()), 2)
    liquidity_pass = avg_daily_value >= config.MIN_VALUE_TRADED

    # ── Step 7: VPVR ──────────────────────────────────────────────────
    vp = build_volume_profile(data)

    # ── Step 8: Breakout ──────────────────────────────────────────────
    lookback = config.BREAKOUT_LOOKBACK
    prev_highs = high.iloc[-(lookback + 1):-1]
    prev_volumes = volume.iloc[-(lookback + 1):-1]
    highest_high_20 = float(prev_highs.max())
    avg_vol_20 = float(prev_volumes.mean())

    breakout_confirmed = (
        last_close > highest_high_20 and last_volume > avg_vol_20
    )

    # ── Step 9: Resistance Distance ───────────────────────────────────
    resistance = find_resistance(high)
    resistance_dist_pct = round(
        ((resistance - last_close) / last_close) * 100, 2
    ) if last_close > 0 else 0
    near_resistance = (
        0 < resistance_dist_pct <= config.RESISTANCE_NEAR_THRESHOLD
    )

    # ── Step 10: ATR Risk ─────────────────────────────────────────────
    atr_val = atr(high, low, close, config.ATR_PERIOD)
    last_atr = round(float(atr_val.iloc[-1]), 2)
    suggested_stop = round(last_close - last_atr, 2)
    risk_pct = round((last_atr / last_close) * 100, 2) if last_close > 0 else 0

    return {
        "ticker": ticker,
        "close": last_close,
        "ema20": last_ema20,
        "ema50": last_ema50,
        "ema200": last_ema200,
        "trend": trend,
        "macd": last_macd,
        "macd_signal": last_macd_signal,
        "macd_hist": last_macd_hist,
        "macd_bullish": macd_bullish,
        "rsi": last_rsi,
        "rsi_pass": rsi_pass,
        "rsi_extended": rsi_extended,
        "rsi_warning": rsi_warning,
        "rvol": rvol,
        "rvol_pass": rvol_pass,
        "avg_daily_value": avg_daily_value,
        "liquidity_pass": liquidity_pass,
        "vp": vp,
        "breakout_confirmed": breakout_confirmed,
        "highest_high_20": round(highest_high_20, 2),
        "resistance": round(resistance, 2),
        "resistance_dist_pct": resistance_dist_pct,
        "near_resistance": near_resistance,
        "atr": last_atr,
        "suggested_stop": suggested_stop,
        "risk_pct": risk_pct,
    }
