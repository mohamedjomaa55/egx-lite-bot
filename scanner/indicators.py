"""
Technical indicators using pure pandas/numpy.
Equivalent to pandas_ta implementations.
"""

import pandas as pd
import numpy as np


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period).mean()


def ema_slope(series: pd.Series, lookback: int = 5) -> pd.Series:
    """Calculate slope of EMA over lookback period as percent change."""
    return (series / series.shift(lookback) - 1) * 100


def find_resistance(high: pd.Series, lookback: int = 20) -> float:
    """Find the nearest resistance level from recent swing highs."""
    recent_highs = high.iloc[-lookback:]
    # Find peaks: points higher than both neighbors
    highs_vals = recent_highs.values
    peaks = []
    for i in range(1, len(highs_vals) - 1):
        if highs_vals[i] > highs_vals[i - 1] and highs_vals[i] > highs_vals[i + 1]:
            peaks.append(highs_vals[i])
    if not peaks:
        # Fallback: use the max of recent highs
        return float(recent_highs.max())
    return float(max(peaks))
