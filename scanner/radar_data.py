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
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
import pytz

from . import config
from .data_provider import fetch_history, fetch_live_quote, to_yahoo

logger = logging.getLogger(__name__)

CAIRO_TZ = pytz.timezone("Africa/Cairo")


# ─── EGX Session Detection ──────────────────────────────────────────
def get_expected_latest_egx_session(now_cairo: Optional[datetime] = None) -> datetime:
    """
    Calculate the expected latest completed EGX session.

    EGX rules:
      - Trading days: Sunday(0) to Thursday(4)
      - Session: 09:30 - 14:15 Cairo time
      - Safety buffer: 30 minutes after close before declaring session complete

    Parameters
    ----------
    now_cairo : datetime, optional
        Current time in Cairo timezone. If None, uses system time.

    Returns
    -------
    datetime
        Date of the expected latest completed session (date only, time is midnight Cairo).
    """
    if now_cairo is None:
        now_cairo = datetime.now(CAIRO_TZ)
    elif now_cairo.tzinfo is None:
        now_cairo = CAIRO_TZ.localize(now_cairo)

    close_time = now_cairo.replace(
        hour=config.EGX_CLOSE_HOUR,
        minute=config.EGX_CLOSE_MINUTE,
        second=0,
        microsecond=0,
    )
    session_complete_time = close_time + timedelta(minutes=config.EGX_SAFETY_BUFFER_MINUTES)

    if now_cairo < session_complete_time:
        check_date = (now_cairo - timedelta(days=1)).date()
    else:
        if now_cairo.weekday() in config.EGX_TRADING_DAYS:
            check_date = now_cairo.date()
        else:
            check_date = (now_cairo - timedelta(days=1)).date()

    for _ in range(7):
        if check_date.weekday() in config.EGX_TRADING_DAYS:
            return datetime.combine(check_date, datetime.min.time()).replace(tzinfo=CAIRO_TZ)
        check_date -= timedelta(days=1)

    return datetime.combine(check_date, datetime.min.time()).replace(tzinfo=CAIRO_TZ)


def is_market_open(now_cairo: Optional[datetime] = None) -> bool:
    """
    Check if EGX market is currently in a trading session.

    EGX session: 09:30 - 14:15 Cairo time, on trading days (Sun-Thu).
    """
    if now_cairo is None:
        now_cairo = datetime.now(CAIRO_TZ)
    elif now_cairo.tzinfo is None:
        now_cairo = CAIRO_TZ.localize(now_cairo)

    if now_cairo.weekday() not in config.EGX_TRADING_DAYS:
        return False

    session_start = now_cairo.replace(
        hour=config.EGX_OPEN_HOUR,
        minute=config.EGX_OPEN_MINUTE,
        second=0,
        microsecond=0,
    )
    session_end = now_cairo.replace(
        hour=config.EGX_CLOSE_HOUR,
        minute=config.EGX_CLOSE_MINUTE,
        second=0,
        microsecond=0,
    )

    return session_start <= now_cairo <= session_end


def assess_data_freshness(
    provider_latest_date_str: str,
    now_cairo: Optional[datetime] = None,
) -> tuple[str, str, int]:
    """
    Assess whether provider data matches the expected latest EGX session.

    Checks in order:
      1. Market currently open → MARKET_OPEN
      2. Non-trading day → NON_TRADING_DAY
      3. Date match → CURRENT or PROVIDER_DELAYED

    Parameters
    ----------
    provider_latest_date_str : str
        Latest date from provider in YYYY-MM-DD format.
    now_cairo : datetime, optional
        Current Cairo time. If None, uses system time.

    Returns
    -------
    tuple[str, str, int]
        (freshness_status, freshness_note, delay_days)
    """
    if now_cairo is None:
        now_cairo = datetime.now(CAIRO_TZ)
    elif now_cairo.tzinfo is None:
        now_cairo = CAIRO_TZ.localize(now_cairo)

    try:
        provider_date = datetime.strptime(provider_latest_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return config.FRESHNESS_DATA_UNAVAILABLE, "Provider date unparseable", -1

    # ── 1. Market currently in session ────────────────────────────────
    if is_market_open(now_cairo):
        expected = get_expected_latest_egx_session(now_cairo)
        delay_days = (expected.date() - provider_date).days
        return config.FRESHNESS_MARKET_OPEN, "Market session in progress", delay_days

    # ── 2. Non-trading day (Friday, Saturday, or holiday) ─────────────
    if now_cairo.weekday() not in config.EGX_TRADING_DAYS:
        expected = get_expected_latest_egx_session(now_cairo)
        expected_date = expected.date()
        delay_days = (expected_date - provider_date).days
        if delay_days <= 0:
            note = f"Non-trading day ({now_cairo.strftime('%A')}), data up to date"
        else:
            note = f"Non-trading day ({now_cairo.strftime('%A')}), provider delayed {delay_days} day(s)"
        return config.FRESHNESS_NON_TRADING_DAY, note, delay_days

    # ── 3. Trading day, market closed — compare dates ─────────────────
    expected = get_expected_latest_egx_session(now_cairo)
    expected_date = expected.date()

    if provider_date == expected_date:
        return config.FRESHNESS_CURRENT, "Data matches expected session", 0

    delay_days = (expected_date - provider_date).days

    if delay_days < 0:
        return config.FRESHNESS_CURRENT, "Provider has future data", 0

    if delay_days <= config.FRESHNESS_MAX_ACCEPTABLE_DELAY_DAYS:
        return config.FRESHNESS_CURRENT, f"Data is {delay_days} day(s) old (acceptable)", delay_days

    return config.FRESHNESS_PROVIDER_DELAYED, f"Provider delayed {delay_days} day(s)", delay_days


# ─── Data Integrity Constants ────────────────────────────────────────
FAILURE_INVALID_OHLC = "INVALID_OHLC"
FAILURE_INVALID_CLOSE = "INVALID_CLOSE"


# ─── Data Models ──────────────────────────────────────────────────────
@dataclass
class DailyBar:
    """
    Canonical completed daily bar model.

    close must always mean the official close of that completed session.
    open must only mean the session opening price.
    """
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    adjusted_close: Optional[float] = None
    is_complete: bool = True
    source: str = "yfinance"


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
    provider_latest_date: str = ""
    expected_latest_session: str = ""
    freshness_status: str = config.FRESHNESS_CURRENT
    freshness_note: str = ""
    freshness_delay_days: int = 0


# ─── OHLC Validation ─────────────────────────────────────────────────
def _validate_bar(bar: DailyBar, symbol: str = "") -> bool:
    """
    Validate a DailyBar for integrity.

    Rules:
      - low <= open <= high  (with tolerance)
      - low <= close <= high (with tolerance)
      - close > 0
      - volume >= 0
      - If invalid, returns False.

    Uses 1.0% tolerance for Yahoo Finance rounding quirks
    (EGX stocks often have open slightly below low or above high).
    """
    if bar.close <= 0:
        logger.debug("Radar: %s invalid close %.4f on %s", symbol, bar.close, bar.date)
        return False

    if bar.volume < 0:
        logger.debug("Radar: %s negative volume on %s", symbol, bar.date)
        return False

    if bar.high <= 0 or bar.low <= 0:
        logger.debug("Radar: %s invalid high/low on %s", symbol, bar.date)
        return False

    # Yahoo Finance EGX data commonly has open/close slightly outside high/low
    # Use 1.0% of price as tolerance (not range-based, to handle small ranges)
    mid_price = (bar.high + bar.low) / 2
    tolerance = mid_price * 0.01  # 1.0% of mid price

    if bar.open < bar.low - tolerance or bar.open > bar.high + tolerance:
        logger.debug(
            "Radar: %s open=%.2f outside [low=%.2f, high=%.2f] (tol=%.2f) on %s",
            symbol, bar.open, bar.low, bar.high, tolerance, bar.date,
        )
        return False

    if bar.close < bar.low - tolerance or bar.close > bar.high + tolerance:
        logger.debug(
            "Radar: %s close=%.2f outside [low=%.2f, high=%.2f] (tol=%.2f) on %s",
            symbol, bar.close, bar.low, bar.high, tolerance, bar.date,
        )
        return False

    return True


def _bars_are_valid(bars: list[DailyBar], symbol: str = "") -> Optional[str]:
    """
    Validate and filter a series of bars.

    Checks:
      - Each bar passes OHLC validation (invalid bars are filtered out)
      - close > 0
      - No duplicate dates
      - Latest bar's close is positive

    Returns None if valid (bars list may be shortened), error string if fatal.
    """
    if not bars:
        return config.FAILURE_DATA_UNAVAILABLE

    seen_dates: set[str] = set()
    filtered: list[DailyBar] = []

    for bar in bars:
        if bar.date in seen_dates:
            logger.debug("Radar: %s duplicate date %s, skipping", symbol, bar.date)
            continue
        seen_dates.add(bar.date)

        if _validate_bar(bar, symbol):
            filtered.append(bar)
        else:
            logger.debug("Radar: %s invalid bar on %s, filtering out", symbol, bar.date)

    if not filtered:
        return config.FAILURE_DATA_UNAVAILABLE

    # Latest bar must have valid close
    latest = filtered[-1]
    if latest.close <= 0 or not np.isfinite(latest.close):
        return FAILURE_INVALID_CLOSE

    # Replace original list with filtered list
    bars.clear()
    bars.extend(filtered)

    return None


# ─── Provider Interface ──────────────────────────────────────────────
def get_completed_daily_bars(
    symbol: str,
    min_candles: int = 60,
    period: str = "1y",
) -> RadarHistory:
    """
    Fetch completed daily OHLCV bars for a symbol.

    Rules:
      - Sort candles chronologically.
      - Remove duplicate dates.
      - The latest candle's CLOSE is latest_close.
      - The immediately preceding candle's CLOSE is previous_close.
      - If close is missing or invalid, return INVALID_CLOSE.

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

        raw_close = row.get("Close")
        raw_open = row.get("Open")
        raw_high = row.get("High")
        raw_low = row.get("Low")
        raw_volume = row.get("Volume", 0)

        # Reject bars with missing or zero close
        if raw_close is None or (isinstance(raw_close, float) and math.isnan(raw_close)) or raw_close == 0:
            logger.debug("Radar: %s missing/zero close on %s, skipping bar", symbol, date_str)
            continue

        # Reject bars with missing OHLC
        if any(
            v is None or (isinstance(v, float) and math.isnan(v))
            for v in [raw_open, raw_high, raw_low]
        ):
            logger.debug("Radar: %s incomplete OHLC on %s, skipping bar", symbol, date_str)
            continue

        bar = DailyBar(
            date=date_str,
            open=float(raw_open),
            high=float(raw_high),
            low=float(raw_low),
            close=float(raw_close),
            volume=int(raw_volume),
            source="yfinance",
        )
        bars.append(bar)

    # Validate bar series
    error = _bars_are_valid(bars, symbol)
    if error:
        return RadarHistory(
            symbol=symbol,
            error=error,
        )

    if len(bars) < min_candles:
        return RadarHistory(
            symbol=symbol,
            error=f"{config.FAILURE_INSUFFICIENT_HISTORY}: {len(bars)} valid candles (need {min_candles})",
        )

    # ── Assess data freshness ──────────────────────────────────────────
    provider_date = bars[-1].date
    expected = get_expected_latest_egx_session()
    expected_date = expected.strftime("%Y-%m-%d")
    freshness_status, freshness_note, delay_days = assess_data_freshness(provider_date)

    return RadarHistory(
        symbol=symbol,
        bars=bars,
        data_mode=config.DATA_MODE_DAILY,
        source="yfinance",
        provider_latest_date=provider_date,
        expected_latest_session=expected_date,
        freshness_status=freshness_status,
        freshness_note=freshness_note,
        freshness_delay_days=delay_days,
    )


def get_live_quote(symbol: str) -> Optional[RadarQuote]:
    """
    Fetch the latest quote for a symbol.

    Currently returns the last completed daily session data.
    Future: return real-time intraday data when a verified provider exists.

    Rules:
      - close = latest completed bar's Close
      - previous_close = previous completed bar's Close
      - Never substitute open for close.
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

    # Validate close is positive and reasonable
    if close <= 0:
        logger.debug("Radar: %s invalid close %.4f in get_live_quote", symbol, close)
        return None

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
