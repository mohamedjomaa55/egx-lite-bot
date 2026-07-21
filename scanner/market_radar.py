"""
Market Radar — Activity Detection Engine
=========================================

Detects unusual volume activity, buying pressure, selling pressure,
and inconclusive activity across EGX stocks.

This module does NOT make trading decisions.
It answers: Which stocks have unusual activity, and what kind?

Usage
-----
    from scanner.market_radar import run_market_radar
    result = run_market_radar()
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from . import config
from .data_provider import get_all_tickers
from .indicators import rsi as calc_rsi, macd as calc_macd, ema as calc_ema
from .radar_data import get_completed_daily_bars, get_live_quote, RadarHistory, RadarQuote

logger = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────
class ActivityCategory:
    BUYING = "BUYING_ACTIVITY"
    SELLING = "SELLING_ACTIVITY"
    UNUSUAL = "UNUSUAL_ACTIVITY"


class ActivityLevel:
    EXTREME = "EXTREME"
    HIGH = "HIGH"
    ELEVATED = "ELEVATED"
    NORMAL = "NORMAL"


# Level sort order (lower = higher priority)
_LEVEL_ORDER = {
    ActivityLevel.EXTREME: 0,
    ActivityLevel.HIGH: 1,
    ActivityLevel.ELEVATED: 2,
    ActivityLevel.NORMAL: 3,
}


# ─── Data Models ──────────────────────────────────────────────────────
@dataclass
class RadarItem:
    symbol: str
    company_name: str = ""
    price: float = 0.0
    price_date: str = ""
    price_change_percent: float = 0.0
    volume: int = 0
    average_volume_20: float = 0.0
    median_volume_20: float = 0.0
    rvol_20: float = 0.0
    traded_value: float = 0.0
    average_traded_value_20: float = 0.0
    rsi_14: float = 50.0
    rsi_previous: float = 50.0
    rsi_change: float = 0.0
    macd_histogram: float = 0.0
    macd_histogram_previous: float = 0.0
    macd_histogram_change: float = 0.0
    macd_line: float = 0.0
    macd_signal_line: float = 0.0
    adx_14: Optional[float] = None
    activity_score: int = 0
    activity_score_components: dict = field(default_factory=dict)
    activity_category: str = ""
    activity_level: str = ""
    activity_label: str = ""
    reasons: list[str] = field(default_factory=list)
    data_mode: str = config.DATA_MODE_DAILY
    source: str = "yfinance"
    is_live: bool = False
    eligible_for_ism: bool = True
    candle_body_percent: float = 0.0
    close_location_value: float = 0.5
    volume_percentile_60: float = 50.0
    price_return_5d: Optional[float] = None
    price_return_20d: Optional[float] = None


@dataclass
class RadarStats:
    symbols_scanned: int = 0
    activity_detected: int = 0
    buying_count: int = 0
    selling_count: int = 0
    unusual_count: int = 0
    failed_count: int = 0
    skipped_illiquid: int = 0
    scan_duration: float = 0.0
    failed_tickers: list[dict] = field(default_factory=list)


@dataclass
class MarketRadarResult:
    timestamp: str = ""
    data_mode: str = config.DATA_MODE_DAILY
    data_date: str = ""
    stats: RadarStats = field(default_factory=RadarStats)
    items: list[RadarItem] = field(default_factory=list)
    all_items: list[RadarItem] = field(default_factory=list)


# ─── Per-Symbol Analysis ─────────────────────────────────────────────
def _analyze_symbol(symbol: str) -> Optional[RadarItem]:
    """
    Analyze a single symbol for radar metrics.
    Returns None if the symbol fails validation.
    """
    # ── Fetch data ────────────────────────────────────────────────────
    history = get_completed_daily_bars(
        symbol,
        min_candles=config.RADAR_MIN_HISTORY_CANDLES,
    )
    if history.error:
        logger.debug("Radar: %s skipped — %s", symbol, history.error)
        return None

    bars = history.bars
    if len(bars) < config.RADAR_MIN_HISTORY_CANDLES:
        return None

    # ── Extract arrays ────────────────────────────────────────────────
    closes = np.array([b.close for b in bars], dtype=np.float64)
    highs = np.array([b.high for b in bars], dtype=np.float64)
    lows = np.array([b.low for b in bars], dtype=np.float64)
    volumes = np.array([b.volume for b in bars], dtype=np.float64)
    opens = np.array([b.open for b in bars], dtype=np.float64)

    # ── Latest completed session ──────────────────────────────────────
    latest_close = closes[-1]
    latest_volume = int(volumes[-1])
    latest_high = highs[-1]
    latest_low = lows[-1]
    latest_open = opens[-1]
    price_date = bars[-1].date

    if latest_volume < 0:
        logger.debug("Radar: %s skipped — negative volume", symbol)
        return None

    # ── Previous session ──────────────────────────────────────────────
    prev_close = closes[-2] if len(closes) >= 2 else latest_close
    price_change = latest_close - prev_close
    price_change_pct = (price_change / prev_close * 100) if prev_close != 0 else 0.0

    # ── Liquidity filter ──────────────────────────────────────────────
    traded_values = closes * volumes
    avg_traded_value_20 = float(np.mean(traded_values[-20:]))
    if avg_traded_value_20 < config.RADAR_MIN_AVG_TRADED_VALUE_20:
        logger.debug(
            "Radar: %s skipped — avg traded value %.0f < %.0f",
            symbol, avg_traded_value_20, config.RADAR_MIN_AVG_TRADED_VALUE_20,
        )
        return None

    if latest_close < config.RADAR_MIN_PRICE:
        logger.debug("Radar: %s skipped — price %.2f < %.2f", symbol, latest_close, config.RADAR_MIN_PRICE)
        return None

    # ── Volume metrics ────────────────────────────────────────────────
    avg_vol_20 = float(np.mean(volumes[-21:-1]))  # exclude latest
    median_vol_20 = float(np.median(volumes[-21:-1]))
    rvol = (latest_volume / avg_vol_20) if avg_vol_20 > 0 else 0.0

    # Volume percentile over 60 sessions
    vol_window = volumes[-60:] if len(volumes) >= 60 else volumes
    volume_percentile = float(np.sum(vol_window <= latest_volume) / len(vol_window) * 100)

    # ── Traded value metrics ──────────────────────────────────────────
    latest_traded_value = latest_close * latest_volume
    avg_traded_value_20_calc = float(np.mean(traded_values[-20:]))
    traded_value_ratio = (latest_traded_value / avg_traded_value_20_calc) if avg_traded_value_20_calc > 0 else 0.0

    # ── RSI ───────────────────────────────────────────────────────────
    import pandas as pd
    close_series = pd.Series(closes)
    rsi_series = calc_rsi(close_series, config.RADAR_RSI_LENGTH)
    rsi_14 = float(rsi_series.iloc[-1]) if not np.isnan(rsi_series.iloc[-1]) else 50.0
    rsi_prev = float(rsi_series.iloc[-2]) if len(rsi_series) >= 2 and not np.isnan(rsi_series.iloc[-2]) else rsi_14
    rsi_change = rsi_14 - rsi_prev

    # ── MACD ──────────────────────────────────────────────────────────
    macd_line, macd_signal, macd_hist = calc_macd(
        close_series, config.RADAR_MACD_FAST, config.RADAR_MACD_SLOW, config.RADAR_MACD_SIGNAL,
    )
    macd_hist_val = float(macd_hist.iloc[-1])
    macd_hist_prev = float(macd_hist.iloc[-2]) if len(macd_hist) >= 2 else macd_hist_val
    macd_hist_change = macd_hist_val - macd_hist_prev
    macd_line_val = float(macd_line.iloc[-1])
    macd_signal_val = float(macd_signal.iloc[-1])

    # ── ADX (optional context) ────────────────────────────────────────
    adx_14 = None
    if config.RADAR_ENABLE_ADX_CONTEXT:
        adx_14 = _calculate_adx(highs, lows, closes, 14)

    # ── Candle metrics ────────────────────────────────────────────────
    body_range = abs(latest_close - latest_open)
    full_range = latest_high - latest_low
    candle_body_pct = (body_range / full_range * 100) if full_range > 0 else 0.0

    clv_denom = latest_high - latest_low
    close_location_value = (
        (latest_close - latest_low) / clv_denom
        if clv_denom > 0
        else 0.5
    )

    # ── Price returns ─────────────────────────────────────────────────
    price_return_5d = None
    if len(closes) >= 6:
        price_return_5d = round((closes[-1] / closes[-6] - 1) * 100, 2)

    price_return_20d = None
    if len(closes) >= 21:
        price_return_20d = round((closes[-1] / closes[-21] - 1) * 100, 2)

    # ── Company name ──────────────────────────────────────────────────
    company_name = config.STOCK_NAMES.get(symbol, symbol)

    # ── Build RadarItem ───────────────────────────────────────────────
    item = RadarItem(
        symbol=symbol,
        company_name=company_name,
        price=round(latest_close, 2),
        price_date=price_date,
        price_change_percent=round(price_change_pct, 2),
        volume=latest_volume,
        average_volume_20=round(avg_vol_20, 0),
        median_volume_20=round(median_vol_20, 0),
        rvol_20=round(rvol, 2),
        traded_value=round(latest_traded_value, 0),
        average_traded_value_20=round(avg_traded_value_20_calc, 0),
        rsi_14=round(rsi_14, 1),
        rsi_previous=round(rsi_prev, 1),
        rsi_change=round(rsi_change, 1),
        macd_histogram=round(macd_hist_val, 4),
        macd_histogram_previous=round(macd_hist_prev, 4),
        macd_histogram_change=round(macd_hist_change, 4),
        macd_line=round(macd_line_val, 4),
        macd_signal_line=round(macd_signal_val, 4),
        adx_14=round(adx_14, 1) if adx_14 is not None else None,
        data_mode=config.DATA_MODE_DAILY,
        source="yfinance",
        is_live=False,
        candle_body_percent=round(candle_body_pct, 1),
        close_location_value=round(close_location_value, 3),
        volume_percentile_60=round(volume_percentile, 1),
        price_return_5d=price_return_5d,
        price_return_20d=price_return_20d,
    )

    # ── Score ─────────────────────────────────────────────────────────
    item.activity_score, item.activity_score_components = _calculate_activity_score(item, rvol, volume_percentile, traded_value_ratio)

    # ── Level ─────────────────────────────────────────────────────────
    item.activity_level = _classify_level(rvol, volume_percentile)

    # ── Category ──────────────────────────────────────────────────────
    item.activity_category, item.activity_label = _classify_category(item)

    # ── Reasons ───────────────────────────────────────────────────────
    item.reasons = _generate_reasons(item, rvol, traded_value_ratio, avg_vol_20)

    return item


# ─── ADX Calculation ─────────────────────────────────────────────────
def _calculate_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> Optional[float]:
    """Calculate Average Directional Index."""
    if len(highs) < period + 1:
        return None
    try:
        plus_dm = np.zeros(len(highs))
        minus_dm = np.zeros(len(highs))
        tr = np.zeros(len(highs))

        for i in range(1, len(highs)):
            up = highs[i] - highs[i - 1]
            down = lows[i - 1] - lows[i]
            plus_dm[i] = up if up > down and up > 0 else 0
            minus_dm[i] = down if down > up and down > 0 else 0
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )

        atr = _wilder_smooth(tr, period)
        plus_di = _wilder_smooth(plus_dm, period)
        minus_di = _wilder_smooth(minus_dm, period)

        # Avoid division by zero
        safe_atr = np.where(atr == 0, 1e-10, atr)
        plus_di_pct = (plus_di / safe_atr) * 100
        minus_di_pct = (minus_di / safe_atr) * 100
        di_sum = plus_di_pct + minus_di_pct
        safe_sum = np.where(di_sum == 0, 1e-10, di_sum)
        dx = (np.abs(plus_di_pct - minus_di_pct) / safe_sum) * 100

        adx = _wilder_smooth(dx, period)
        last_adx = float(adx[-1])
        return last_adx if not np.isnan(last_adx) else None
    except Exception:
        return None


def _wilder_smooth(data: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing method."""
    result = np.full_like(data, np.nan, dtype=np.float64)
    if len(data) < period:
        return result
    result[period - 1] = np.mean(data[:period])
    for i in range(period, len(data)):
        result[i] = (result[i - 1] * (period - 1) + data[i]) / period
    return result


# ─── Activity Score (0-100) ──────────────────────────────────────────
def _calculate_activity_score(
    item: RadarItem,
    rvol: float,
    volume_percentile: float,
    traded_value_ratio: float,
) -> tuple[int, dict]:
    """
    Calculate activity score from 0 to 100.

    Components:
      - volume_score:       50 pts (RVOL + volume percentile)
      - liquidity_score:    15 pts (traded value quality)
      - price_volume_score: 15 pts (candle location, body, returns)
      - rsi_score:          10 pts (RSI momentum information)
      - macd_score:         10 pts (MACD histogram momentum)
    """
    components = {}

    # ── Volume Score (50 pts) ────────────────────────────────────────
    # RVOL contribution (30 pts)
    if rvol >= config.RADAR_EXTREME_RVOL:
        rvol_pts = 30
    elif rvol >= config.RADAR_HIGH_RVOL:
        rvol_pts = 24
    elif rvol >= config.RADAR_MIN_RVOL:
        rvol_pts = 18
    elif rvol >= 1.0:
        rvol_pts = 10
    else:
        rvol_pts = 3

    # Volume percentile contribution (20 pts)
    if volume_percentile >= config.RADAR_PERCENTILE_EXTREME:
        vp_pts = 20
    elif volume_percentile >= config.RADAR_PERCENTILE_HIGH:
        vp_pts = 16
    elif volume_percentile >= config.RADAR_PERCENTILE_ELEVATED:
        vp_pts = 12
    elif volume_percentile >= 50:
        vp_pts = 6
    else:
        vp_pts = 2

    volume_score = min(config.RADAR_SCORE_VOLUME, rvol_pts + vp_pts)
    components["volume_score"] = volume_score

    # ── Liquidity Score (15 pts) ─────────────────────────────────────
    if traded_value_ratio >= 2.0:
        liq_pts = 15
    elif traded_value_ratio >= 1.5:
        liq_pts = 12
    elif traded_value_ratio >= 1.2:
        liq_pts = 9
    elif traded_value_ratio >= 1.0:
        liq_pts = 6
    else:
        liq_pts = 2

    liquidity_score = min(config.RADAR_SCORE_LIQUIDITY, liq_pts)
    components["liquidity_score"] = liquidity_score

    # ── Price-Volume Score (15 pts) ──────────────────────────────────
    pv_score = 0
    # Close location value: high = bullish close, low = bearish close
    # Both extremes indicate strong activity
    clv = item.close_location_value
    if clv > 0.7 or clv < 0.3:
        pv_score += 5  # close at extreme of range
    elif clv > 0.6 or clv < 0.4:
        pv_score += 3

    # Large candle body relative to range = directional conviction
    if item.candle_body_percent > 70:
        pv_score += 5
    elif item.candle_body_percent > 50:
        pv_score += 3

    # 5-day return shows recent momentum context
    if item.price_return_5d is not None:
        abs_ret = abs(item.price_return_5d)
        if abs_ret > 5:
            pv_score += 5
        elif abs_ret > 2:
            pv_score += 3
        elif abs_ret > 1:
            pv_score += 1

    price_volume_score = min(config.RADAR_SCORE_PRICE_VOLUME, pv_score)
    components["price_volume_score"] = price_volume_score

    # ── RSI Score (10 pts) ───────────────────────────────────────────
    rsi_score = 0
    # Extreme RSI indicates strong momentum
    if item.rsi_14 > 70 or item.rsi_14 < 30:
        rsi_score += 5
    elif item.rsi_14 > 60 or item.rsi_14 < 40:
        rsi_score += 3

    # RSI change direction adds momentum context
    abs_rsi_change = abs(item.rsi_change)
    if abs_rsi_change > 5:
        rsi_score += 5
    elif abs_rsi_change > 2:
        rsi_score += 3
    elif abs_rsi_change > 1:
        rsi_score += 1

    rsi_score = min(config.RADAR_SCORE_RSI, rsi_score)
    components["rsi_score"] = rsi_score

    # ── MACD Score (10 pts) ──────────────────────────────────────────
    macd_score = 0
    # Large absolute histogram = strong momentum
    abs_hist = abs(item.macd_histogram)
    if abs_hist > 2.0:
        macd_score += 5
    elif abs_hist > 1.0:
        macd_score += 3
    elif abs_hist > 0.5:
        macd_score += 1

    # Histogram change = momentum shift
    abs_hist_change = abs(item.macd_histogram_change)
    if abs_hist_change > 1.0:
        macd_score += 5
    elif abs_hist_change > 0.5:
        macd_score += 3
    elif abs_hist_change > 0.2:
        macd_score += 1

    macd_score = min(config.RADAR_SCORE_MACD, macd_score)
    components["macd_score"] = macd_score

    # ── Total ────────────────────────────────────────────────────────
    total = volume_score + liquidity_score + price_volume_score + rsi_score + macd_score
    total = max(0, min(100, total))

    return total, components


# ─── Activity Level ──────────────────────────────────────────────────
def _classify_level(rvol: float, volume_percentile: float) -> str:
    """Classify activity level based on RVOL and volume percentile."""
    if rvol >= config.RADAR_EXTREME_RVOL or volume_percentile >= config.RADAR_PERCENTILE_EXTREME:
        return ActivityLevel.EXTREME
    if rvol >= config.RADAR_HIGH_RVOL or volume_percentile >= config.RADAR_PERCENTILE_HIGH:
        return ActivityLevel.HIGH
    if rvol >= config.RADAR_MIN_RVOL or volume_percentile >= config.RADAR_PERCENTILE_ELEVATED:
        return ActivityLevel.ELEVATED
    return ActivityLevel.NORMAL


# ─── Activity Category ───────────────────────────────────────────────
def _classify_category(item: RadarItem) -> tuple[str, str]:
    """
    Classify activity into BUYING_ACTIVITY, SELLING_ACTIVITY, or UNUSUAL_ACTIVITY.

    Uses multiple signals:
      - price change direction
      - close location value (where price closed in the session range)
      - RSI direction
      - MACD histogram direction
      - candle body size

    Returns (category, label).
    """
    bullish_signals = 0
    bearish_signals = 0
    total_weight = 0

    # ── Price change direction (weight: 2) ───────────────────────────
    if item.price_change_percent > 0.5:
        bullish_signals += 2
    elif item.price_change_percent < -0.5:
        bearish_signals += 2
    elif item.price_change_percent > 0:
        bullish_signals += 1
    elif item.price_change_percent < 0:
        bearish_signals += 1
    total_weight += 2

    # ── Close location value (weight: 2) ─────────────────────────────
    if item.close_location_value > 0.7:
        bullish_signals += 2
    elif item.close_location_value < 0.3:
        bearish_signals += 2
    elif item.close_location_value > 0.55:
        bullish_signals += 1
    elif item.close_location_value < 0.45:
        bearish_signals += 1
    total_weight += 2

    # ── RSI change direction (weight: 1) ─────────────────────────────
    if item.rsi_change > 1:
        bullish_signals += 1
    elif item.rsi_change < -1:
        bearish_signals += 1
    total_weight += 1

    # ── MACD histogram change (weight: 2) ────────────────────────────
    if item.macd_histogram_change > 0.05:
        bullish_signals += 2
    elif item.macd_histogram_change < -0.05:
        bearish_signals += 2
    total_weight += 2

    # ── Candle body dominance (weight: 1) ────────────────────────────
    if item.candle_body_percent > 60:
        if item.close_location_value > 0.5:
            bullish_signals += 1
        else:
            bearish_signals += 1
    total_weight += 1

    # ── Decision ──────────────────────────────────────────────────────
    bull_ratio = bullish_signals / total_weight if total_weight > 0 else 0
    bear_ratio = bearish_signals / total_weight if total_weight > 0 else 0

    if bull_ratio >= 0.6 and bullish_signals >= 3:
        return ActivityCategory.BUYING, _buying_label(item)
    elif bear_ratio >= 0.6 and bearish_signals >= 3:
        return ActivityCategory.SELLING, _selling_label(item)
    else:
        return ActivityCategory.UNUSUAL, "Unusual activity — direction unclear"


def _buying_label(item: RadarItem) -> str:
    """Generate a buying activity label."""
    if item.rvol_20 >= config.RADAR_EXTREME_RVOL:
        return "Strong buying activity"
    elif item.rvol_20 >= config.RADAR_HIGH_RVOL:
        return "Possible accumulation"
    else:
        return "Moderate buying activity"


def _selling_label(item: RadarItem) -> str:
    """Generate a selling activity label."""
    if item.rvol_20 >= config.RADAR_EXTREME_RVOL:
        return "Strong selling pressure"
    elif item.rvol_20 >= config.RADAR_HIGH_RVOL:
        return "Possible distribution"
    else:
        return "Moderate selling activity"


# ─── Radar Reasons ───────────────────────────────────────────────────
def _generate_reasons(
    item: RadarItem,
    rvol: float,
    traded_value_ratio: float,
    avg_vol: float,
) -> list[str]:
    """Generate 2-4 short factual reasons per stock."""
    reasons = []

    # RVOL
    if rvol >= 1.35:
        reasons.append(f"RVOL {rvol:.1f}x versus {config.RADAR_VOLUME_AVERAGE_LENGTH}-day average")
    elif rvol >= 1.0:
        reasons.append(f"Volume {rvol:.1f}x average")

    # Traded value
    if traded_value_ratio >= 1.5:
        reasons.append(f"Traded value {traded_value_ratio:.1f}x above normal")
    elif traded_value_ratio < 0.7:
        reasons.append(f"Traded value below average")

    # Close location
    if item.close_location_value >= 0.8:
        reasons.append("Close finished near the session high")
    elif item.close_location_value <= 0.2:
        reasons.append("Close finished near the session low")

    # Price movement
    if abs(item.price_change_percent) < 0.3 and rvol >= 1.5:
        reasons.append("High volume with limited price movement")
    elif item.price_change_percent > 3:
        reasons.append(f"Price rose {item.price_change_percent:.1f}% on active trading")
    elif item.price_change_percent < -3:
        reasons.append(f"Price fell {abs(item.price_change_percent):.1f}% on active trading")

    # RSI direction
    if abs(item.rsi_change) > 3:
        direction = "rose" if item.rsi_change > 0 else "fell"
        reasons.append(f"RSI {direction} from {item.rsi_previous:.0f} to {item.rsi_14:.0f}")

    # MACD histogram
    if abs(item.macd_histogram_change) > 0.3:
        if item.macd_histogram_change > 0:
            reasons.append("MACD histogram improving")
        else:
            reasons.append("MACD histogram weakening")

    # Ensure we have at least 2 reasons
    if len(reasons) < 2:
        if item.volume_percentile_60 > 80:
            reasons.append(f"Volume at {item.volume_percentile_60:.0f}th percentile of 60-session range")
        if item.price_return_5d is not None:
            if abs(item.price_return_5d) > 3:
                reasons.append(f"5-day return: {item.price_return_5d:+.1f}%")

    return reasons[:4]


# ─── Main Entry Point ────────────────────────────────────────────────
def run_market_radar(
    symbols: list[str] | None = None,
    top_n: int | None = None,
    min_avg_value: float | None = None,
    force_refresh: bool = False,
) -> MarketRadarResult:
    """
    Run the Market Radar scan across all configured EGX symbols.

    Parameters
    ----------
    symbols : list[str] | None
        Symbols to scan. If None, scans all configured symbols.
    top_n : int | None
        Number of top results to return. Default from config.
    min_avg_value : float | None
        Override minimum average traded value filter.
    force_refresh : bool
        If True, clear the data cache before scanning.

    Returns
    -------
    MarketRadarResult
    """
    t0 = time.time()
    if top_n is None:
        top_n = config.RADAR_TOP_N
    if min_avg_value is not None:
        config.RADAR_MIN_AVG_TRADED_VALUE_20 = min_avg_value

    if symbols is None:
        symbols = get_all_tickers()

    stats = RadarStats(symbols_scanned=len(symbols))
    items: list[RadarItem] = []
    failed_tickers: list[dict] = []

    logger.info("Radar: scanning %d symbols...", len(symbols))

    # ── Parallel analysis ─────────────────────────────────────────────
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_sym = {executor.submit(_analyze_symbol, s): s for s in symbols}
        for future in as_completed(future_to_sym):
            sym = future_to_sym[future]
            try:
                item = future.result()
                if item is not None:
                    items.append(item)
                else:
                    stats.skipped_illiquid += 1
            except Exception as exc:
                stats.failed_count += 1
                failed_tickers.append({"ticker": sym, "reason": str(exc)})
                logger.warning("Radar: %s failed — %s", sym, exc)

    elapsed = time.time() - t0
    stats.scan_duration = round(elapsed, 1)
    stats.failed_tickers = failed_tickers

    # ── Classify and count ────────────────────────────────────────────
    for item in items:
        if item.activity_category == ActivityCategory.BUYING:
            stats.buying_count += 1
        elif item.activity_category == ActivityCategory.SELLING:
            stats.selling_count += 1
        elif item.activity_category == ActivityCategory.UNUSUAL:
            stats.unusual_count += 1

    # ── Sort by level → score → rvol ─────────────────────────────────
    items.sort(
        key=lambda x: (
            _LEVEL_ORDER.get(x.activity_level, 3),
            -x.activity_score,
            -x.rvol_20,
            -x.average_traded_value_20,
        ),
    )

    # ── Filter by level ───────────────────────────────────────────────
    if not config.RADAR_INCLUDE_NORMAL:
        all_items = list(items)
        items = [i for i in items if i.activity_level != ActivityLevel.NORMAL]
    else:
        all_items = items

    stats.activity_detected = len(items)

    # ── Data date ─────────────────────────────────────────────────────
    data_date = ""
    if all_items:
        dates = [i.price_date for i in all_items if i.price_date]
        if dates:
            data_date = max(dates)

    # ── Build result ──────────────────────────────────────────────────
    result = MarketRadarResult(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        data_mode=config.DATA_MODE_DAILY,
        data_date=data_date,
        stats=stats,
        items=items[:top_n],
        all_items=all_items,
    )

    logger.info(
        "Radar: done — %d symbols in %.1fs, %d with activity "
        "(%d buying, %d selling, %d unusual)",
        stats.symbols_scanned,
        elapsed,
        stats.activity_detected,
        stats.buying_count,
        stats.selling_count,
        stats.unusual_count,
    )

    return result
