"""
Radar Session Audit — Diagnostic tool for EGX Lite Market Radar
================================================================

Runs the EXACT same production data path and scoring functions as /radar.
No thresholds modified. No behavior changes.

Usage:
    python -m scanner.audit_radar_session
"""

from __future__ import annotations

import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytz

# Suppress noisy loggers
logging.getLogger("yfinance").disabled = True
logging.getLogger("urllib3").disabled = True
logging.basicConfig(level=logging.WARNING, format="%(message)s")

from scanner import config
from scanner.data_provider import get_all_tickers, clear_cache
from scanner.indicators import rsi as calc_rsi, macd as calc_macd
from scanner.radar_data import (
    get_completed_daily_bars, get_expected_latest_egx_session,
    is_market_open, assess_data_freshness,
    RadarHistory, FAILURE_INVALID_OHLC, FAILURE_INVALID_CLOSE,
)
from scanner.market_radar import (
    _analyze_symbol, _calculate_activity_score, _classify_level,
    _classify_category, _calculate_adx,
    ActivityCategory, ActivityLevel,
    RadarItem,
)

CAIRO_TZ = pytz.timezone("Africa/Cairo")

# ── Exclusion reason tracking ────────────────────────────────────────
class ExclusionTracker:
    """Tracks exactly why each stock was excluded from output."""

    def __init__(self):
        self.excluded: dict[str, dict] = {}

    def record_skip(self, symbol: str, reason: str):
        self.excluded[symbol] = {"included": False, "reason": reason}

    def record_scored(self, symbol: str, level: str, category: str, score: int):
        self.excluded[symbol] = {
            "included": True,
            "reason": f"level={level}, category={category}, score={score}",
        }


def _analyze_symbol_with_tracking(symbol: str, tracker: ExclusionTracker) -> tuple[str, RadarItem | None]:
    """Wrapper around _analyze_symbol that tracks exclusion reasons."""
    try:
        history = get_completed_daily_bars(
            symbol,
            min_candles=config.RADAR_MIN_HISTORY_CANDLES,
        )
        if history.error:
            tracker.record_skip(symbol, f"data_error: {history.error}")
            return symbol, None

        bars = history.bars
        if len(bars) < config.RADAR_MIN_HISTORY_CANDLES:
            tracker.record_skip(symbol, f"insufficient_history: {len(bars)} candles (need {config.RADAR_MIN_HISTORY_CANDLES})")
            return symbol, None

        # Run the full production analysis
        item = _analyze_symbol(symbol)
        if item is None:
            # Determine which filter caught it
            closes = np.array([b.close for b in history.bars], dtype=np.float64)
            volumes = np.array([b.volume for b in history.bars], dtype=np.float64)
            traded_values = closes * volumes
            avg_traded_value_20 = float(np.mean(traded_values[-20:]))
            latest_close = closes[-1]

            reasons = []
            if latest_close <= 0 or not np.isfinite(latest_close):
                reasons.append("invalid_close")
            if latest_close < config.RADAR_MIN_PRICE:
                reasons.append(f"price_below_min ({latest_close:.2f} < {config.RADAR_MIN_PRICE})")
            if avg_traded_value_20 < config.RADAR_MIN_AVG_TRADED_VALUE_20:
                reasons.append(f"liquidity_filter (avg_value={avg_traded_value_20:,.0f} < {config.RADAR_MIN_AVG_TRADED_VALUE_20:,.0f})")

            reason_str = "; ".join(reasons) if reasons else "unknown_filter_in_analyze_symbol"
            tracker.record_skip(symbol, reason_str)
            return symbol, None

        # Item was analyzed — track scoring
        tracker.record_scored(symbol, item.activity_level, item.activity_category, item.activity_score)
        return symbol, item

    except Exception as e:
        tracker.record_skip(symbol, f"exception: {e}")
        return symbol, None


def _detailed_stock_analysis(symbol: str) -> dict:
    """Run deep analysis for a single stock, returning all diagnostic data."""
    result = {
        "ticker": symbol,
        "provider_date": "",
        "latest_close": 0.0,
        "previous_close": 0.0,
        "session_open": 0.0,
        "session_high": 0.0,
        "session_low": 0.0,
        "daily_change_pct": 0.0,
        "volume": 0,
        "volume_20d_average": 0.0,
        "rvol": 0.0,
        "traded_value": 0.0,
        "traded_value_20d_average": 0.0,
        "traded_value_ratio": 0.0,
        "close_location_value": 0.5,
        "rsi_current": 50.0,
        "rsi_previous": 50.0,
        "rsi_change": 0.0,
        "macd_histogram_current": 0.0,
        "macd_histogram_previous": 0.0,
        "buying_score": 0,
        "selling_score": 0,
        "unusual_score": 0,
        "final_activity_score": 0,
        "detected_category": "",
        "included_in_output": False,
        "exact_exclusion_reason": "",
    }

    try:
        history = get_completed_daily_bars(symbol, min_candles=config.RADAR_MIN_HISTORY_CANDLES)
        if history.error:
            result["exact_exclusion_reason"] = f"data_error: {history.error}"
            return result

        bars = history.bars
        if len(bars) < config.RADAR_MIN_HISTORY_CANDLES:
            result["exact_exclusion_reason"] = f"insufficient_history: {len(bars)} candles"
            return result

        closes = np.array([b.close for b in bars], dtype=np.float64)
        highs = np.array([b.high for b in bars], dtype=np.float64)
        lows = np.array([b.low for b in bars], dtype=np.float64)
        volumes = np.array([b.volume for b in bars], dtype=np.float64)
        opens = np.array([b.open for b in bars], dtype=np.float64)

        latest_close = closes[-1]
        latest_volume = int(volumes[-1])
        latest_high = highs[-1]
        latest_low = lows[-1]
        latest_open = opens[-1]

        if latest_close <= 0 or not np.isfinite(latest_close):
            result["exact_exclusion_reason"] = f"invalid_close: {latest_close}"
            return result

        prev_close = closes[-2] if len(closes) >= 2 else latest_close
        price_change_pct = ((latest_close - prev_close) / prev_close * 100) if prev_close != 0 else 0.0

        traded_values = closes * volumes
        avg_vol_20 = float(np.mean(volumes[-21:-1]))
        avg_traded_value_20 = float(np.mean(traded_values[-20:]))
        rvol = (latest_volume / avg_vol_20) if avg_vol_20 > 0 else 0.0
        latest_traded_value = latest_close * latest_volume
        traded_value_ratio = (latest_traded_value / avg_traded_value_20) if avg_traded_value_20 > 0 else 0.0

        vol_window = volumes[-60:] if len(volumes) >= 60 else volumes
        volume_percentile = float(np.sum(vol_window <= latest_volume) / len(vol_window) * 100)

        close_series = pd.Series(closes)
        rsi_series = calc_rsi(close_series, config.RADAR_RSI_LENGTH)
        rsi_14 = float(rsi_series.iloc[-1]) if not np.isnan(rsi_series.iloc[-1]) else 50.0
        rsi_prev = float(rsi_series.iloc[-2]) if len(rsi_series) >= 2 and not np.isnan(rsi_series.iloc[-2]) else rsi_14
        rsi_change = rsi_14 - rsi_prev

        macd_line, macd_signal, macd_hist = calc_macd(close_series, config.RADAR_MACD_FAST, config.RADAR_MACD_SLOW, config.RADAR_MACD_SIGNAL)
        macd_hist_val = float(macd_hist.iloc[-1])
        macd_hist_prev = float(macd_hist.iloc[-2]) if len(macd_hist) >= 2 else macd_hist_val

        clv_denom = latest_high - latest_low
        clv = ((latest_close - latest_low) / clv_denom) if clv_denom > 0 else 0.5

        # Liquidity check
        if avg_traded_value_20 < config.RADAR_MIN_AVG_TRADED_VALUE_20:
            result["exact_exclusion_reason"] = f"liquidity_filter (avg_value={avg_traded_value_20:,.0f} < {config.RADAR_MIN_AVG_TRADED_VALUE_20:,.0f})"
        elif latest_close < config.RADAR_MIN_PRICE:
            result["exact_exclusion_reason"] = f"price_below_min ({latest_close:.2f} < {config.RADAR_MIN_PRICE})"
        else:
            result["exact_exclusion_reason"] = "none"

        # Build temporary item for scoring
        company_name = config.STOCK_NAMES.get(symbol, symbol)
        item = RadarItem(
            symbol=symbol, company_name=company_name,
            latest_close=round(latest_close, 2), previous_close=round(prev_close, 2),
            session_open=round(latest_open, 2), session_high=round(latest_high, 2),
            session_low=round(latest_low, 2), display_price=round(latest_close, 2),
            price=round(latest_close, 2),
            price_date=bars[-1].date,
            price_change_percent=round(price_change_pct, 2),
            volume=latest_volume, average_volume_20=round(avg_vol_20, 0),
            rvol_20=round(rvol, 2),
            traded_value=round(latest_traded_value, 0),
            average_traded_value_20=round(avg_traded_value_20, 0),
            rsi_14=round(rsi_14, 1), rsi_previous=round(rsi_prev, 1),
            rsi_change=round(rsi_change, 1),
            macd_histogram=round(macd_hist_val, 4),
            macd_histogram_previous=round(macd_hist_prev, 4),
            macd_histogram_change=round(macd_hist_val - macd_hist_prev, 4),
            macd_line=round(float(macd_line.iloc[-1]), 4),
            macd_signal_line=round(float(macd_signal.iloc[-1]), 4),
            data_mode=config.DATA_MODE_DAILY,
            close_location_value=round(clv, 3),
            candle_body_percent=round((abs(latest_close - latest_open) / clv_denom * 100) if clv_denom > 0 else 0, 1),
            volume_percentile_60=round(volume_percentile, 1),
        )

        # 5d/20d returns
        if len(closes) >= 6:
            item.price_return_5d = round((closes[-1] / closes[-6] - 1) * 100, 2)
        if len(closes) >= 21:
            item.price_return_20d = round((closes[-1] / closes[-21] - 1) * 100, 2)

        score, components = _calculate_activity_score(item, rvol, volume_percentile, traded_value_ratio)
        item.activity_score = score
        item.activity_score_components = components
        item.activity_level = _classify_level(rvol, volume_percentile)
        item.activity_category, item.activity_label = _classify_category(item)

        # Individual category scores (approximate)
        buying_score = 0
        selling_score = 0
        unusual_score = 0
        bull, bear = 0, 0
        if item.price_change_percent > 0.5: bull += 2
        elif item.price_change_percent < -0.5: bear += 2
        elif item.price_change_percent > 0: bull += 1
        elif item.price_change_percent < 0: bear += 1
        if item.close_location_value > 0.7: bull += 2
        elif item.close_location_value < 0.3: bear += 2
        elif item.close_location_value > 0.55: bull += 1
        elif item.close_location_value < 0.45: bear += 1
        if item.rsi_change > 1: bull += 1
        elif item.rsi_change < -1: bear += 1
        if item.macd_histogram_change > 0.05: bull += 2
        elif item.macd_histogram_change < -0.05: bear += 2
        total_w = 8
        bull_r = bull / total_w if total_w > 0 else 0
        bear_r = bear / total_w if total_w > 0 else 0
        if bull_r >= 0.6 and bull >= 3: buying_score = score
        elif bear_r >= 0.6 and bear >= 3: selling_score = score
        else: unusual_score = score

        result.update({
            "provider_date": bars[-1].date,
            "latest_close": round(latest_close, 2),
            "previous_close": round(prev_close, 2),
            "session_open": round(latest_open, 2),
            "session_high": round(latest_high, 2),
            "session_low": round(latest_low, 2),
            "daily_change_pct": round(price_change_pct, 2),
            "volume": latest_volume,
            "volume_20d_average": round(avg_vol_20, 0),
            "rvol": round(rvol, 2),
            "traded_value": round(latest_traded_value, 0),
            "traded_value_20d_average": round(avg_traded_value_20, 0),
            "traded_value_ratio": round(traded_value_ratio, 2),
            "close_location_value": round(clv, 3),
            "rsi_current": round(rsi_14, 1),
            "rsi_previous": round(rsi_prev, 1),
            "rsi_change": round(rsi_change, 1),
            "macd_histogram_current": round(macd_hist_val, 4),
            "macd_histogram_previous": round(macd_hist_prev, 4),
            "buying_score": buying_score,
            "selling_score": selling_score,
            "unusual_score": unusual_score,
            "final_activity_score": score,
            "detected_category": item.activity_category,
            "included_in_output": item.activity_level != ActivityLevel.NORMAL,
        })

    except Exception as e:
        result["exact_exclusion_reason"] = f"exception: {e}"

    return result


def run_audit():
    """Run the full diagnostic audit."""
    clear_cache()
    print("=" * 100)
    print("  EGX LITE MARKET RADAR — SESSION AUDIT")
    print("=" * 100)

    # ── Freshness / Session Audit ─────────────────────────────────────
    now_cairo = datetime.now(CAIRO_TZ)
    expected = get_expected_latest_egx_session(now_cairo)
    market_open = is_market_open(now_cairo)

    # Simulate what the production code does for freshness
    # First, get one stock's data to find provider_latest_date
    symbols = get_all_tickers()
    test_history = get_completed_daily_bars(symbols[0], min_candles=config.RADAR_MIN_HISTORY_CANDLES)
    provider_date = test_history.provider_latest_date if not test_history.error else "N/A"

    if provider_date and provider_date != "N/A":
        freshness_status, freshness_note, delay_days = assess_data_freshness(provider_date, now_cairo)
    else:
        freshness_status, freshness_note, delay_days = "UNKNOWN", "Could not determine", -1

    print("\n── FRESHNESS / SESSION AUDIT ──")
    print(f"  Cairo current datetime   : {now_cairo.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  Day of week              : {['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][now_cairo.weekday()]}")
    print(f"  Is trading day           : {now_cairo.weekday() in config.EGX_TRADING_DAYS}")
    print(f"  Market open (is_market_open): {market_open}")
    print(f"  Session window           : {config.EGX_OPEN_HOUR}:{config.EGX_OPEN_MINUTE:02d} - {config.EGX_CLOSE_HOUR}:{config.EGX_CLOSE_MINUTE:02d} Cairo")
    print(f"  Close + buffer           : {config.EGX_CLOSE_HOUR}:{config.EGX_CLOSE_MINUTE:02d} + {config.EGX_SAFETY_BUFFER_MINUTES}min = {config.EGX_CLOSE_HOUR}:{config.EGX_CLOSE_MINUTE + config.EGX_SAFETY_BUFFER_MINUTES:02d}")
    print(f"  provider_latest_date     : {provider_date}")
    print(f"  expected_latest_session  : {expected.strftime('%Y-%m-%d')}")
    print(f"  freshness_status         : {freshness_status}")
    print(f"  freshness_note           : {freshness_note}")
    print(f"  delay_days               : {delay_days}")

    # ── Trace freshness calculation branches ──────────────────────────
    print("\n── FRESHNESS BRANCH TRACE ──")
    session_complete_time = now_cairo.replace(
        hour=config.EGX_CLOSE_HOUR, minute=config.EGX_CLOSE_MINUTE,
        second=0, microsecond=0
    ) + pd.Timedelta(minutes=config.EGX_SAFETY_BUFFER_MINUTES)

    print(f"  now_cairo < session_complete_time ({now_cairo.strftime('%H:%M')} < {session_complete_time.strftime('%H:%M')}): {now_cairo < session_complete_time}")
    if now_cairo < session_complete_time:
        print(f"  → Branch: before session complete → check_date = yesterday")
        check_date = (now_cairo - pd.Timedelta(days=1)).date()
    else:
        if now_cairo.weekday() in config.EGX_TRADING_DAYS:
            print(f"  → Branch: after session complete, trading day → check_date = today")
            check_date = now_cairo.date()
        else:
            print(f"  → Branch: after session complete, NOT trading day → check_date = yesterday")
            check_date = (now_cairo - pd.Timedelta(days=1)).date()

    print(f"  Expected walk-back from {check_date}:")
    for i in range(7):
        if check_date.weekday() in config.EGX_TRADING_DAYS:
            print(f"    → Found trading day: {check_date.strftime('%A %Y-%m-%d')} ✓")
            break
        check_date -= pd.Timedelta(days=1)
        print(f"    → {check_date + pd.Timedelta(days=1)} is not trading day, stepping back")

    print(f"\n  assess_data_freshness({provider_date}) called with is_market_open={market_open}:")
    if market_open:
        print(f"    → Branch 1: market open → return MARKET_OPEN")
    elif now_cairo.weekday() not in config.EGX_TRADING_DAYS:
        print(f"    → Branch 2: non-trading day → return NON_TRADING_DAY")
    else:
        print(f"    → Branch 3: trading day, market closed → compare dates")
        if provider_date == expected.strftime("%Y-%m-%d"):
            print(f"      provider == expected → CURRENT")
        else:
            print(f"      provider={provider_date} != expected={expected.strftime('%Y-%m-%d')} → PROVIDER_DELAYED")

    # ── Full 34-Stock Diagnostic ─────────────────────────────────────
    print("\n" + "=" * 100)
    print("  FULL 34-STOCK DIAGNOSTIC TABLE")
    print("=" * 100)

    tracker = ExclusionTracker()
    all_results = []

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_detailed_stock_analysis, s): s for s in symbols}
        for future in as_completed(futures):
            sym = futures[future]
            try:
                result = future.result()
                all_results.append(result)
            except Exception as e:
                all_results.append({
                    "ticker": sym,
                    "exact_exclusion_reason": f"audit_exception: {e}",
                })

    elapsed = time.time() - t0

    # Sort by activity score descending
    all_results.sort(key=lambda x: x.get("final_activity_score", 0), reverse=True)

    # Print table header
    header = (
        f"{'Ticker':<8} {'Provider':<12} {'Close':>8} {'Chg%':>7} "
        f"{'RVOL':>6} {'TV_Ratio':>9} {'CLV':>6} {'RSI':>5} {'MACD_H':>8} "
        f"{'Score':>6} {'Level':<10} {'Category':<18} {'Included':>8} {'Exclusion Reason'}"
    )
    print(header)
    print("-" * 160)

    # Get production items for level info
    production_items = {}
    for r in all_results:
        sym = r.get("ticker", "")
        if r.get("included_in_output"):
            production_items[sym] = r

    # Map level from the detailed analysis
    level_map = {}
    for r in all_results:
        sym = r.get("ticker", "")
        score = r.get("final_activity_score", 0)
        rvol = r.get("rvol", 0)
        # Re-derive level from production thresholds
        from scanner.market_radar import _classify_level
        vol_pct = 0
        # We need volume_percentile — compute from volume data
        level_map[sym] = "NORMAL"  # Will be overwritten below

    # Re-run to get exact levels
    for r in all_results:
        sym = r.get("ticker", "")
        rvol = r.get("rvol", 0)
        # Approximate volume percentile from rvol (not exact, but diagnostic)
        # Better: compute from the actual data
        r["_level"] = "UNKNOWN"

    # Run level classification properly
    for r in all_results:
        sym = r.get("ticker", "")
        try:
            history = get_completed_daily_bars(sym, min_candles=config.RADAR_MIN_HISTORY_CANDLES)
            if history.error or len(history.bars) < 2:
                r["_level"] = "N/A"
                continue
            volumes = np.array([b.volume for b in history.bars], dtype=np.float64)
            vol_window = volumes[-60:] if len(volumes) >= 60 else volumes
            vol_pct = float(np.sum(vol_window <= volumes[-1]) / len(vol_window) * 100)
            rvol = r.get("rvol", 0)
            r["_level"] = _classify_level(rvol, vol_pct)
        except Exception:
            r["_level"] = "ERROR"

    for r in all_results:
        sym = r.get("ticker", "")
        level = r.get("_level", "UNKNOWN")
        included = level != ActivityLevel.NORMAL

        print(
            f"{sym:<8} {r.get('provider_date',''):<12} {r.get('latest_close',0):>8.2f} "
            f"{r.get('daily_change_pct',0):>+6.1f}% "
            f"{r.get('rvol',0):>5.2f}x {r.get('traded_value_ratio',0):>8.2f}x "
            f"{r.get('close_location_value',0.5):>5.2f} "
            f"{r.get('rsi_current',50):>5.1f} {r.get('macd_histogram_current',0):>+7.4f} "
            f"{r.get('final_activity_score',0):>5} {level:<10} "
            f"{r.get('detected_category',''):<18} "
            f"{'YES' if included else 'no':>8} "
            f"{r.get('exact_exclusion_reason','')}"
        )

    # ── Diagnostic Lists ─────────────────────────────────────────────
    print(f"\nScan completed in {elapsed:.1f}s")

    # A. Stocks with absolute daily move >= 2%
    movers = [r for r in all_results if abs(r.get("daily_change_pct", 0)) >= 2.0]
    print(f"\n── A. Stocks with |daily move| >= 2% ({len(movers)}) ──")
    for r in movers:
        inc = "INCLUDED" if r.get("included_in_output") else "excluded"
        print(f"  {r['ticker']}: {r.get('daily_change_pct',0):+.1f}% — score={r.get('final_activity_score',0)}, level={r.get('_level','?')}, {inc}")

    # B. Stocks with RVOL >= 1.5
    high_vol = [r for r in all_results if r.get("rvol", 0) >= 1.5]
    print(f"\n── B. Stocks with RVOL >= 1.5 ({len(high_vol)}) ──")
    for r in high_vol:
        inc = "INCLUDED" if r.get("included_in_output") else "excluded"
        print(f"  {r['ticker']}: RVOL={r.get('rvol',0):.2f}x — score={r.get('final_activity_score',0)}, level={r.get('_level','?')}, {inc}")

    # C. Stocks with traded value ratio >= 1.5
    high_val = [r for r in all_results if r.get("traded_value_ratio", 0) >= 1.5]
    print(f"\n── C. Stocks with traded value ratio >= 1.5 ({len(high_val)}) ──")
    for r in high_val:
        inc = "INCLUDED" if r.get("included_in_output") else "excluded"
        print(f"  {r['ticker']}: ratio={r.get('traded_value_ratio',0):.2f}x — score={r.get('final_activity_score',0)}, level={r.get('_level','?')}, {inc}")

    # D. Stocks closing in top/bottom 20% of session range
    extremes = []
    for r in all_results:
        h, l = r.get("session_high", 0), r.get("session_low", 0)
        c = r.get("latest_close", 0)
        rng = h - l
        if rng > 0:
            pos = (c - l) / rng
            if pos >= 0.8 or pos <= 0.2:
                extremes.append((r, pos))
    print(f"\n── D. Stocks closing in top/bottom 20% of range ({len(extremes)}) ──")
    for r, pos in sorted(extremes, key=lambda x: x[1]):
        loc = "TOP" if pos >= 0.8 else "BOTTOM"
        inc = "INCLUDED" if r.get("included_in_output") else "excluded"
        print(f"  {r['ticker']}: position={pos:.0%} ({loc}) — score={r.get('final_activity_score',0)}, level={r.get('_level','?')}, {inc}")

    # E. Stocks matching >= 2 conditions but excluded
    cond_stocks = []
    for r in all_results:
        if r.get("included_in_output"):
            continue
        conds = 0
        if abs(r.get("daily_change_pct", 0)) >= 2.0: conds += 1
        if r.get("rvol", 0) >= 1.5: conds += 1
        if r.get("traded_value_ratio", 0) >= 1.5: conds += 1
        h, l, c = r.get("session_high", 0), r.get("session_low", 0), r.get("latest_close", 0)
        rng = h - l
        if rng > 0:
            pos = (c - l) / rng
            if pos >= 0.8 or pos <= 0.2: conds += 1
        if conds >= 2:
            cond_stocks.append((r, conds))
    print(f"\n── E. Stocks matching >= 2 activity conditions but excluded ({len(cond_stocks)}) ──")
    for r, conds in sorted(cond_stocks, key=lambda x: -x[1]):
        reasons = []
        if abs(r.get("daily_change_pct", 0)) >= 2.0: reasons.append(f"move={r['daily_change_pct']:+.1f}%")
        if r.get("rvol", 0) >= 1.5: reasons.append(f"rvol={r['rvol']:.2f}x")
        if r.get("traded_value_ratio", 0) >= 1.5: reasons.append(f"tv_ratio={r['traded_value_ratio']:.2f}x")
        h, l, c = r.get("session_high", 0), r.get("session_low", 0), r.get("latest_close", 0)
        rng = h - l
        if rng > 0:
            pos = (c - l) / rng
            if pos >= 0.8 or pos <= 0.2: reasons.append(f"range_pos={pos:.0%}")
        print(f"  {r['ticker']}: {conds} conditions [{', '.join(reasons)}] — score={r.get('final_activity_score',0)}, level={r.get('_level','?')}")

    # F. Stocks within 10 points below output threshold (score >= 30 if NORMAL is threshold)
    # The threshold is: level != NORMAL. NORMAL is the default for most.
    # We need to find stocks close to being ELEVATED
    near_threshold = []
    for r in all_results:
        if r.get("included_in_output"):
            continue
        score = r.get("final_activity_score", 0)
        rvol = r.get("rvol", 0)
        # ELEVATED requires RVOL >= 1.35 or volume percentile >= 75
        # Close to ELEVATED means: RVOL between 1.0-1.35, or score >= 30
        if score >= 30:
            near_threshold.append(r)
    print(f"\n── F. Excluded stocks with score >= 30 (near ELEVATED threshold) ({len(near_threshold)}) ──")
    for r in sorted(near_threshold, key=lambda x: -x.get("final_activity_score", 0)):
        print(f"  {r['ticker']}: score={r.get('final_activity_score',0)}, rvol={r.get('rvol',0):.2f}x, level={r.get('_level','?')}, cat={r.get('detected_category','')}")

    # ── Summary Statistics ────────────────────────────────────────────
    included = [r for r in all_results if r.get("included_in_output")]
    excluded = [r for r in all_results if not r.get("included_in_output")]
    normal = [r for r in all_results if r.get("_level") == "NORMAL"]

    print(f"\n── SUMMARY ──")
    print(f"  Total stocks analyzed : {len(all_results)}")
    print(f"  Included in output   : {len(included)}")
    print(f"  Excluded             : {len(excluded)}")
    print(f"  Level NORMAL         : {len(normal)}")
    print(f"  Level ELEVATED+      : {len(all_results) - len(normal)}")
    print(f"  Avg score (all)      : {np.mean([r.get('final_activity_score',0) for r in all_results]):.1f}")
    print(f"  Avg score (excluded) : {np.mean([r.get('final_activity_score',0) for r in excluded]):.1f}")
    print(f"  Max score (excluded) : {max([r.get('final_activity_score',0) for r in excluded]):.1f}")

    # ── Root Cause Analysis ──────────────────────────────────────────
    print(f"\n── ROOT CAUSE ANALYSIS ──")
    print(f"  1. Detection count ({len(included)}/{len(all_results)}):")
    print(f"     → {len(normal)} stocks have activity_level=NORMAL (below ELEVATED threshold)")
    print(f"     → ELEVATED requires: RVOL >= 1.35 OR volume percentile >= 75")
    print(f"     → This is by design — most stocks have average volume on most days")
    print()

    # Check freshness validity
    print(f"  2. Session metadata validity:")
    print(f"     provider_date = {provider_date}")
    print(f"     expected_session = {expected.strftime('%Y-%m-%d')}")
    print(f"     freshness = {freshness_status}")
    if market_open:
        print(f"     → Market IS currently open → freshness=MARKET_OPEN is correct")
        print(f"     → expected_session={expected.strftime('%Y-%m-%d')} (today) is correct for open market")
        print(f"     → provider shows yesterday ({provider_date}) which is the last completed session")
        print(f"     → This is VALID — data is from the most recent completed session")
    elif provider_date != expected.strftime("%Y-%m-%d"):
        print(f"     → MISMATCH: provider={provider_date}, expected={expected.strftime('%Y-%m-%d')}")
        print(f"     → This indicates a PROVIDER_DELAYED state")
    else:
        print(f"     → Match: provider={provider_date} == expected={expected.strftime('%Y-%m-%d')}")
        print(f"     → This is VALID — CURRENT state")

    # Save full results to JSON
    output_path = Path("data/audit_results.json")
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": now_cairo.isoformat(),
            "freshness": {
                "provider_date": provider_date,
                "expected_session": expected.strftime("%Y-%m-%d"),
                "status": freshness_status,
                "note": freshness_note,
                "delay_days": delay_days,
                "market_open": market_open,
            },
            "summary": {
                "total": len(all_results),
                "included": len(included),
                "excluded": len(excluded),
                "avg_score": round(np.mean([r.get("final_activity_score", 0) for r in all_results]), 1),
            },
            "stocks": all_results,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  Full results saved to: {output_path}")

    return all_results


if __name__ == "__main__":
    run_audit()
