from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(slots=True)
class FilterResult:
    """Container for the pass/reject decision of a stock against filters."""

    passed: bool
    warning: str = ""


def apply_filters(
    df: pd.DataFrame,
    *,
    ema50: pd.Series,
    ema200: pd.Series,
    macd: pd.DataFrame,
    rsi: pd.Series,
    volume_avg: float,
    liquidity_threshold: float,
    price_poc: float,
    breakout: bool,
    min_rsi: float,
    max_rsi: float,
) -> FilterResult:
    """Apply the market-quality screen rules and return a decision."""
    close = float(df["Close"].iloc[-1])
    volume = float(df["Volume"].iloc[-1])
    liquidity_proxy = close * volume

    if close < float(ema200.iloc[-1]):
        return FilterResult(False, "Trend: close below EMA200")
    if float(ema50.iloc[-1]) <= float(ema200.iloc[-1]):
        return FilterResult(False, "Trend: EMA50 <= EMA200")

    if not (float(macd["MACD"].iloc[-1]) > float(macd["Signal"].iloc[-1])):
        return FilterResult(False, "MACD: line not above signal")
    if float(macd["Histogram"].iloc[-1]) < 0:
        return FilterResult(False, "MACD: histogram negative")

    rsi_value = float(rsi.iloc[-1])
    if rsi_value < min_rsi:
        return FilterResult(False, "RSI: below 55")
    if rsi_value > max_rsi:
        return FilterResult(True, "Extended")

    if volume <= volume_avg:
        return FilterResult(False, "Volume: below 20-day average")
    if liquidity_proxy < liquidity_threshold:
        return FilterResult(False, "Liquidity: below threshold")
    if close <= price_poc:
        return FilterResult(False, "VPVR: price not above POC")
    if breakout is False:
        return FilterResult(False, "Breakout: no 20-session breakout")

    return FilterResult(True)
