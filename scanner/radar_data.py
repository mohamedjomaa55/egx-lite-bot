"""
Radar Data Abstraction Layer
============================

Provides a clean interface for fetching price/volume data.
Currently uses daily completed session data from Yahoo Finance.

Designed so the data timeframe can later be switched to:
  - DAILY_COMPLETED_SESSION (current)
  - LIVE_SESSION
  - INTRADAY_60M
  - INTRADAY_15M

without requiring a redesign of the market radar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from . import config
from .data_provider import fetch_history, fetch_live_quote, to_yahoo

logger = logging.getLogger(__name__)


# ─── Data Models ──────────────────────────────────────────────────────
@dataclass
class DailyBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class RadarQuote:
    symbol: str
    close: float
    previous_close: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[int] = None
    traded_value: Optional[float] = None
    data_mode: str = config.DATA_MODE_DAILY
    price_date: Optional[str] = None
    source: str = "yfinance"
    is_live: bool = False
    data_age: Optional[str] = None


@dataclass
class RadarHistory:
    symbol: str
    bars: list[DailyBar] = field(default_factory=list)
    data_mode: str = config.DATA_MODE_DAILY
    source: str = "yfinance"
    error: Optional[str] = None


# ─── Provider Interface ──────────────────────────────────────────────
def get_completed_daily_bars(
    symbol: str,
    min_candles: int = 60,
    period: str = "1y",
) -> RadarHistory:
    """
    Fetch completed daily OHLCV bars for a symbol.

    Parameters
    ----------
    symbol : str
        EGX ticker (e.g. "ARCC").
    min_candles : int
        Minimum number of candles required.
    period : str
        Yahoo Finance period string.

    Returns
    -------
    RadarHistory
    """
    try:
        df = fetch_history(symbol)
    except Exception as exc:
        logger.warning("Radar: history fetch failed for %s: %s", symbol, exc)
        return RadarHistory(
            symbol=symbol,
            error=f"{config.FAILURE_PROVIDER_ERROR}: {exc}",
        )

    if df is None or df.empty:
        return RadarHistory(
            symbol=symbol,
            error=config.FAILURE_DATA_UNAVAILABLE,
        )

    if len(df) < min_candles:
        return RadarHistory(
            symbol=symbol,
            error=f"{config.FAILURE_INSUFFICIENT_HISTORY}: {len(df)} candles (need {min_candles})",
        )

    bars: list[DailyBar] = []
    for idx, row in df.iterrows():
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
        bars.append(DailyBar(
            date=date_str,
            open=float(row.get("Open", 0)),
            high=float(row.get("High", 0)),
            low=float(row.get("Low", 0)),
            close=float(row.get("Close", 0)),
            volume=int(row.get("Volume", 0)),
        ))

    return RadarHistory(
        symbol=symbol,
        bars=bars,
        data_mode=config.DATA_MODE_DAILY,
        source="yfinance",
    )


def get_live_quote(symbol: str) -> Optional[RadarQuote]:
    """
    Fetch the latest quote for a symbol.

    Currently returns the last completed daily session data.
    Future: return real-time intraday data when a verified provider exists.
    """
    try:
        hist = fetch_history(symbol)
    except Exception:
        return None

    if hist is None or hist.empty or len(hist) < 2:
        return None

    last = hist.iloc[-1]
    prev = hist.iloc[-2]

    close = float(last["Close"])
    prev_close = float(prev["Close"])
    volume = int(last.get("Volume", 0))
    high = float(last.get("High", close))
    low = float(last.get("Low", close))
    opn = float(last.get("Open", close))
    traded_value = close * volume

    date_str = hist.index[-1].strftime("%Y-%m-%d") if hasattr(hist.index[-1], "strftime") else str(hist.index[-1])

    return RadarQuote(
        symbol=symbol,
        close=close,
        previous_close=prev_close,
        open=opn,
        high=high,
        low=low,
        volume=volume,
        traded_value=traded_value,
        data_mode=config.DATA_MODE_DAILY,
        price_date=date_str,
        source="yfinance",
        is_live=False,
    )


def get_intraday_bars(
    symbol: str,
    interval: str = "15m",
    limit: int = 100,
) -> list:
    """
    Placeholder for future intraday data.
    Currently returns empty list.
    """
    logger.debug(
        "Radar: intraday bars not available for %s (interval=%s). "
        "Using daily data only.",
        symbol, interval,
    )
    return []
