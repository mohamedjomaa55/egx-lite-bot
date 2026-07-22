"""
Data Provenance Audit — EGX Lite Market Radar
=============================================

Traces the exact source and session date of every input used in
activity scoring during MARKET_OPEN.

Usage:
    python -m scanner.audit_data_provenance
"""

from __future__ import annotations

import json
import logging
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pytz

logging.getLogger("yfinance").disabled = True
logging.getLogger("urllib3").disabled = True
logging.getLogger("httpx").disabled = True
logging.basicConfig(level=logging.WARNING, format="%(message)s")

from scanner import config
from scanner.data_provider import (
    fetch_history, get_all_tickers, clear_cache,
    _is_market_hours, _tv_batch_fetch, _TV_CACHE, _CACHE,
    normalize_ticker,
)
from scanner.indicators import rsi as calc_rsi, macd as calc_macd
from scanner.radar_data import (
    get_completed_daily_bars, get_expected_latest_egx_session,
    is_market_open, assess_data_freshness, _validate_bar, DailyBar,
)

try:
    import yfinance as yf
except ImportError:
    yf = None

CAIRO_TZ = pytz.timezone("Africa/Cairo")


@dataclass
class FieldProvenance:
    field_name: str
    value: float | int | str | None
    source: str  # "yfinance", "tradingview", "calculated", "indicator"
    provider_date: str  # YYYY-MM-DD
    is_partial: bool  # True if this is a partial/live session bar


@dataclass
class TickerProvenance:
    ticker: str
    selected_provider: str
    historical_provider: str
    live_overlay_provider: str
    latest_bar_date: str
    latest_bar_is_partial: bool
    close_source: FieldProvenance = None
    open_source: FieldProvenance = None
    high_source: FieldProvenance = None
    low_source: FieldProvenance = None
    volume_source: FieldProvenance = None
    previous_close_source: FieldProvenance = None
    rsi_input: str = ""
    rsi_date_range: str = ""
    macd_input: str = ""
    macd_date_range: str = ""
    avg_volume_20d_source: str = ""
    avg_volume_20d_date_range: str = ""
    traded_value_source: str = ""
    traded_value_date: str = ""
    final_bar: str = ""
    bars_before_overlay: int = 0
    bars_after_overlay: int = 0
    overlay_action: str = ""  # "appended", "updated", "none"
    yahoo_last_3: str = ""
    tv_payload: str = ""
    normalized_before: str = ""
    normalized_after: str = ""
    consistency_flags: list = field(default_factory=list)


def _fetch_yahoo_only(ticker: str) -> pd.DataFrame:
    """Fetch Yahoo Finance data WITHOUT TradingView overlay (raw)."""
    yahoo_sym = normalize_ticker(ticker)
    if not yahoo_sym or yf is None:
        return pd.DataFrame()
    try:
        return yf.Ticker(yahoo_sym).history(period=config.DATA_PERIOD, interval=config.DATA_INTERVAL)
    except Exception:
        return pd.DataFrame()


def _trace_ticker_provenance(ticker: str, tv_cache: dict) -> TickerProvenance:
    """Trace exact data provenance for a single ticker."""
    prov = TickerProvenance(
        ticker=ticker,
        selected_provider="",
        historical_provider="",
        live_overlay_provider="",
        latest_bar_date="",
        latest_bar_is_partial=False,
    )

    now_cairo = datetime.now(CAIRO_TZ)
    today = now_cairo.date()
    market_open = _is_market_hours()

    # ── Step 1: Raw Yahoo data ───────────────────────────────────────
    yahoo_df = _fetch_yahoo_only(ticker)
    if yahoo_df.empty:
        prov.consistency_flags.append("YAHOO_DATA_EMPTY")
        return prov

    prov.historical_provider = "yfinance"
    yahoo_last_3 = yahoo_df.tail(3)
    rows = []
    for idx, row in yahoo_last_3.iterrows():
        d = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
        rows.append(f"    {d}: O={row['Open']:.2f} H={row['High']:.2f} L={row['Low']:.2f} C={row['Close']:.2f} V={int(row['Volume'])}")
    prov.yahoo_last_3 = "\n".join(rows)
    yahoo_last_date = yahoo_df.index[-1].date() if len(yahoo_df) > 0 else None

    # ── Step 2: TradingView payload ──────────────────────────────────
    tv_data = tv_cache.get(ticker)
    if tv_data:
        prov.live_overlay_provider = "tradingview"
        prov.tv_payload = (
            f"    close={tv_data.get('close')}, open={tv_data.get('open')}, "
            f"high={tv_data.get('high')}, low={tv_data.get('low')}, "
            f"volume={tv_data.get('volume')}, prev={tv_data.get('previous_close')}"
        )
    else:
        prov.tv_payload = "    (no TradingView data available)"

    # ── Step 3: Fetch via production path (with overlay) ─────────────
    clear_cache()
    prod_df = fetch_history(ticker)

    # Determine overlay action
    if market_open and tv_data and tv_data.get("close"):
        if yahoo_last_date == today:
            prov.overlay_action = "updated_in_place"
            prov.bars_before_overlay = len(yahoo_df)
            prov.bars_after_overlay = len(prod_df)
        else:
            prov.overlay_action = "appended"
            prov.bars_before_overlay = len(yahoo_df)
            prov.bars_after_overlay = len(prod_df)
    else:
        prov.overlay_action = "none (market closed or no TV data)"
        prov.bars_before_overlay = len(yahoo_df)
        prov.bars_after_overlay = len(prod_df)

    # ── Step 4: Normalized bars before overlay (Yahoo only) ──────────
    before_rows = []
    for idx, row in yahoo_df.tail(3).iterrows():
        d = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
        before_rows.append(f"    {d}: O={row['Open']:.2f} H={row['High']:.2f} L={row['Low']:.2f} C={row['Close']:.2f} V={int(row['Volume'])}")
    prov.normalized_before = "\n".join(before_rows)

    # ── Step 5: Normalized bars after overlay (production) ───────────
    after_rows = []
    for idx, row in prod_df.tail(3).iterrows():
        d = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
        after_rows.append(f"    {d}: O={row['Open']:.2f} H={row['High']:.2f} L={row['Low']:.2f} C={row['Close']:.2f} V={int(row['Volume'])}")
    prov.normalized_after = "\n".join(after_rows)

    # ── Step 6: Get completed bars (production path) ─────────────────
    history = get_completed_daily_bars(ticker, min_candles=config.RADAR_MIN_HISTORY_CANDLES)
    if history.error:
        prov.consistency_flags.append(f"DATA_ERROR: {history.error}")
        return prov

    bars = history.bars
    if len(bars) < 2:
        prov.consistency_flags.append("INSUFFICIENT_BARS")
        return prov

    latest_bar = bars[-1]
    prev_bar = bars[-2]
    prov.latest_bar_date = latest_bar.date

    # Is the latest bar partial? (date == today during market hours)
    prov.latest_bar_is_partial = (
        market_open
        and latest_bar.date == today.strftime("%Y-%m-%d")
    )

    # ── Step 7: Per-field source attribution ──────────────────────────
    # Close: always from bars[-1]
    close_is_tv = (
        market_open
        and tv_data
        and tv_data.get("close")
        and (yahoo_last_date != today or latest_bar.date == today.strftime("%Y-%m-%d"))
    )
    close_src = "tradingview" if close_is_tv else "yfinance"
    close_date = latest_bar.date

    prov.close_source = FieldProvenance(
        field_name="close",
        value=latest_bar.close,
        source=close_src,
        provider_date=close_date,
        is_partial=prov.latest_bar_is_partial,
    )

    prov.open_source = FieldProvenance(
        field_name="open",
        value=latest_bar.open,
        source=close_src,
        provider_date=close_date,
        is_partial=prov.latest_bar_is_partial,
    )

    prov.high_source = FieldProvenance(
        field_name="high",
        value=latest_bar.high,
        source=close_src,
        provider_date=close_date,
        is_partial=prov.latest_bar_is_partial,
    )

    prov.low_source = FieldProvenance(
        field_name="low",
        value=latest_bar.low,
        source=close_src,
        provider_date=close_date,
        is_partial=prov.latest_bar_is_partial,
    )

    prov.volume_source = FieldProvenance(
        field_name="volume",
        value=latest_bar.volume,
        source=close_src,
        provider_date=close_date,
        is_partial=prov.latest_bar_is_partial,
    )

    prov.previous_close_source = FieldProvenance(
        field_name="previous_close",
        value=prev_bar.close,
        source="yfinance",
        provider_date=prev_bar.date,
        is_partial=False,
    )

    # ── Step 8: Indicator inputs ─────────────────────────────────────
    closes = np.array([b.close for b in bars], dtype=np.float64)
    volumes = np.array([b.volume for b in bars], dtype=np.float64)

    close_series = pd.Series(closes)
    rsi_series = calc_rsi(close_series, config.RADAR_RSI_LENGTH)
    macd_line, macd_signal, macd_hist = calc_macd(
        close_series, config.RADAR_MACD_FAST, config.RADAR_MACD_SLOW, config.RADAR_MACD_SIGNAL,
    )

    # RSI/MACD input range
    rsi_start = max(0, len(bars) - len(rsi_series))
    rsi_start_date = bars[rsi_start].date if rsi_start < len(bars) else "?"
    prov.rsi_input = f"close_series[{len(closes)} bars]"
    prov.rsi_date_range = f"{rsi_start_date} to {latest_bar.date}"
    prov.macd_input = f"close_series[{len(closes)} bars]"
    prov.macd_date_range = f"{bars[0].date} to {latest_bar.date}"

    # ── Step 9: Volume metrics ───────────────────────────────────────
    avg_vol_20 = float(np.mean(volumes[-21:-1])) if len(volumes) >= 22 else 0.0
    rvol = (latest_bar.volume / avg_vol_20) if avg_vol_20 > 0 else 0.0

    traded_values = closes * volumes
    avg_traded_value_20 = float(np.mean(traded_values[-20:]))
    latest_traded_value = latest_bar.close * latest_bar.volume
    traded_value_ratio = (latest_traded_value / avg_traded_value_20) if avg_traded_value_20 > 0 else 0.0

    prov.avg_volume_20d_source = f"mean(volumes[-21:-1]) = bars[{max(0,len(bars)-21)}] to bars[{len(bars)-2}]"
    prov.avg_volume_20d_date_range = f"{bars[max(0,len(bars)-21)].date} to {bars[-2].date}"
    prov.traded_value_source = f"close[-1] * volume[-1] = {latest_bar.close:.2f} * {latest_bar.volume}"
    prov.traded_value_date = latest_bar.date

    # ── Step 10: Final bar object ────────────────────────────────────
    prov.final_bar = (
        f"DailyBar(date={latest_bar.date}, O={latest_bar.open:.2f}, H={latest_bar.high:.2f}, "
        f"L={latest_bar.low:.2f}, C={latest_bar.close:.2f}, V={latest_bar.volume}, "
        f"source={latest_bar.source})"
    )

    # ── Step 11: Selected provider ───────────────────────────────────
    prov.selected_provider = close_src

    # ── Step 12: Consistency checks ──────────────────────────────────
    if prov.latest_bar_is_partial:
        prov.consistency_flags.append("PARTIAL_SESSION_BAR")

    if close_src == "tradingview" and prev_bar.source == "yfinance":
        prov.consistency_flags.append("MIXED_PROVIDERS: close=TV, prev_close=Yahoo")

    if market_open and tv_data:
        tv_vol = tv_data.get("volume", 0)
        if tv_vol and tv_vol > 0:
            prov.consistency_flags.append(f"LIVE_VOLUME: {tv_vol} (partial session)")

    if yahoo_last_date and yahoo_last_date != today and market_open and tv_data:
        gap_days = (today - yahoo_last_date).days
        if gap_days > 1:
            prov.consistency_flags.append(f"DATA_GAP: Yahoo last={yahoo_last_date}, today={today} ({gap_days} days)")

    # Check for corrupted Yahoo data (zero volume)
    recent_vol = yahoo_df["Volume"].tail(10)
    zero_vol_count = (recent_vol == 0).sum()
    if zero_vol_count >= 3:
        prov.consistency_flags.append(f"CORRUPTED_YAHOO: {zero_vol_count}/10 recent bars have 0 volume")

    # Check if RSI/MACD include partial bar
    if prov.latest_bar_is_partial:
        prov.consistency_flags.append("INDICATORS_INCLUDE_PARTIAL_BAR: RSI/MACD calculated with partial session close")

    return prov


def run_provenance_audit():
    """Run the full data provenance audit."""
    clear_cache()
    print("=" * 100)
    print("  EGX LITE MARKET RADAR — DATA PROVENANCE AUDIT")
    print("=" * 100)

    now_cairo = datetime.now(CAIRO_TZ)
    market_open = _is_market_hours()
    today = now_cairo.date()

    print(f"\n  Cairo time     : {now_cairo.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  Market open    : {market_open}")
    print(f"  Today          : {today}")

    # Fetch TradingView batch
    print("\n  Fetching TradingView batch...")
    tv_cache = _tv_batch_fetch()
    print(f"  TradingView    : {len(tv_cache)} stocks fetched")

    # ── Production call path trace ───────────────────────────────────
    print("\n" + "=" * 100)
    print("  EXACT PRODUCTION CALL PATH (during MARKET_OPEN)")
    print("=" * 100)
    print("""
  1. run_market_radar()
       └─ for each symbol: _analyze_symbol(symbol)
            └─ get_completed_daily_bars(symbol, min_candles=60)
                 └─ fetch_history(symbol)                     [data_provider.py:131]
                      ├─ yf.Ticker(yahoo_sym).history("1y")  [Yahoo Finance]
                      ├─ if _is_market_hours():               [data_provider.py:155]
                      │    ├─ _tv_batch_fetch()               [TradingView scanner]
                      │    └─ if last_bar_date == today:
                      │         UPDATE existing bar OHLCV     [data_provider.py:162-166]
                      │    else:
                      │         APPEND new bar for today      [data_provider.py:169-179]
                      └─ return DataFrame (cached 300s)
                 └─ convert DataFrame → list[DailyBar]        [radar_data.py:384-416]
                      └─ ALL bars: source="yfinance"          [HARDCODED, line 414]
            └─ _analyze_symbol() continues with bars:
                 ├─ closes = [b.close for b in bars]          [includes TV bar]
                 ├─ volumes = [b.volume for b in bars]        [includes TV bar]
                 ├─ latest_close = closes[-1]                 [TradingView close]
                 ├─ prev_close = closes[-2]                   [Yahoo last completed]
                 ├─ avg_vol_20 = mean(volumes[-21:-1])        [Yahoo only, excludes latest]
                 ├─ rvol = volumes[-1] / avg_vol_20           [TV partial / Yahoo full-day]
                 ├─ RSI(closes)                               [includes TV partial close]
                 ├─ MACD(closes)                              [includes TV partial close]
                 └─ _calculate_activity_score(item, rvol, vol_pct, tv_ratio)
    """)

    # ── Focus tickers ────────────────────────────────────────────────
    focus_tickers = ["LCSW", "ETEL", "ACGC", "ORAS"]
    all_tickers = sorted(config.EGX_SYMBOL_MAP.keys())

    for ticker in focus_tickers:
        print(f"\n{'=' * 100}")
        print(f"  DETAILED TRACE: {ticker}")
        print(f"{'=' * 100}")

        prov = _trace_ticker_provenance(ticker, tv_cache)

        print(f"\n  selected_provider       : {prov.selected_provider}")
        print(f"  historical_provider     : {prov.historical_provider}")
        print(f"  live_overlay_provider   : {prov.live_overlay_provider or '(none)'}")
        print(f"  overlay_action          : {prov.overlay_action}")
        print(f"  bars_before_overlay     : {prov.bars_before_overlay}")
        print(f"  bars_after_overlay      : {prov.bars_after_overlay}")
        print(f"  latest_bar_date         : {prov.latest_bar_date}")
        print(f"  latest_bar_is_partial   : {prov.latest_bar_is_partial}")

        print(f"\n  --- Raw Yahoo Finance (last 3 bars) ---")
        print(prov.yahoo_last_3)

        print(f"\n  --- TradingView Payload ---")
        print(prov.tv_payload)

        print(f"\n  --- Normalized Bars BEFORE Overlay (Yahoo only) ---")
        print(prov.normalized_before)

        print(f"\n  --- Normalized Bars AFTER Overlay (production) ---")
        print(prov.normalized_after)

        print(f"\n  --- Per-Field Source Map ---")
        for fp in [prov.close_source, prov.open_source, prov.high_source,
                    prov.low_source, prov.volume_source, prov.previous_close_source]:
            if fp:
                partial_tag = " [PARTIAL]" if fp.is_partial else ""
                print(f"  {fp.field_name:20s} = {fp.value:>12}  source={fp.source:12s}  date={fp.provider_date}{partial_tag}")

        print(f"\n  --- Indicator Inputs ---")
        print(f"  RSI input              : {prov.rsi_input}")
        print(f"  RSI date range         : {prov.rsi_date_range}")
        print(f"  MACD input             : {prov.macd_input}")
        print(f"  MACD date range        : {prov.macd_date_range}")
        print(f"  avg_volume_20d source  : {prov.avg_volume_20d_source}")
        print(f"  avg_volume_20d range   : {prov.avg_volume_20d_date_range}")
        print(f"  traded_value source    : {prov.traded_value_source}")
        print(f"  traded_value date      : {prov.traded_value_date}")

        print(f"\n  --- Final Bar Passed to Scoring ---")
        print(f"  {prov.final_bar}")

        if prov.consistency_flags:
            print(f"\n  --- CONSISTENCY FLAGS ---")
            for flag in prov.consistency_flags:
                print(f"  ! {flag}")

    # ── Full 34-stock summary ────────────────────────────────────────
    print(f"\n{'=' * 100}")
    print("  FULL 34-STOCK PROVENANCE SUMMARY")
    print(f"{'=' * 100}")

    header = (
        f"{'Ticker':<8} {'BarDate':<12} {'Partial':>7} {'CloseSrc':<12} {'VolSrc':<12} "
        f"{'PrevCloseDate':<14} {'Overlay':<12} {'Flags'}"
    )
    print(header)
    print("-" * 130)

    all_provs = []
    for ticker in all_tickers:
        prov = _trace_ticker_provenance(ticker, tv_cache)
        all_provs.append(prov)
        flags = "; ".join(prov.consistency_flags) if prov.consistency_flags else "-"
        close_src = prov.close_source.source if prov.close_source else "?"
        vol_src = prov.volume_source.source if prov.volume_source else "?"
        prev_date = prov.previous_close_source.provider_date if prov.previous_close_source else "?"
        print(
            f"{ticker:<8} {prov.latest_bar_date:<12} {'YES' if prov.latest_bar_is_partial else 'no':>7} "
            f"{close_src:<12} {vol_src:<12} {prev_date:<14} {prov.overlay_action:<12} {flags}"
        )

    # ── Consistency summary ──────────────────────────────────────────
    print(f"\n--- Consistency Flags Summary ---")
    flag_counts = {}
    for prov in all_provs:
        for flag in prov.consistency_flags:
            key = flag.split(":")[0] if ":" in flag else flag
            flag_counts[key] = flag_counts.get(key, 0) + 1
    for flag, count in sorted(flag_counts.items(), key=lambda x: -x[1]):
        print(f"  {flag}: {count} tickers")

    # ── Answers to explicit questions ────────────────────────────────
    print(f"\n{'=' * 100}")
    print("  ANSWERS TO EXPLICIT QUESTIONS")
    print(f"{'=' * 100}")

    print("""
  Q1: During MARKET_OPEN, does current_session_volume represent
      today's partial live volume?

  A1: YES. When _is_market_hours() returns True, fetch_history()
      calls _tv_batch_fetch() and appends/updates a bar with
      TradingView's live volume for today. This bar becomes bars[-1].
      In _analyze_symbol(), latest_volume = bars[-1].volume, which IS
      today's partial session volume from TradingView.
      The 20-day average volume (avg_vol_20) is calculated from
      volumes[-21:-1], which EXCLUDES the latest bar. So avg_vol_20
      is based on Yahoo's historical completed-session volumes only.
      RVOL = partial_today_volume / full_day_historical_average.

  Q2: Is today's TradingView bar appended, replaced, or only used
      for display fields?

  A2: APPENDED (or UPDATED in place). Two code paths in fetch_history():
      - If Yahoo's last bar date == today: UPDATES existing bar OHLCV
        in-place (data_provider.py:162-166)
      - If Yahoo's last bar date != today: APPENDS a new bar for today
        via pd.concat (data_provider.py:169-179)
      The bar is NOT just for display — it becomes part of the bars[]
      list used by _analyze_symbol() for ALL calculations including
      indicators, RVOL, and scoring.

  Q3: Are RSI and MACD recalculated using today's partial bar?

  A3: YES. In _analyze_symbol():
      closes = np.array([b.close for b in bars])  — includes TV bar
      rsi_series = calc_rsi(pd.Series(closes), 14)
      macd_line, macd_signal, macd_hist = calc_macd(pd.Series(closes), ...)
      Both RSI and MACD use the full closes array which includes
      today's partial TradingView close as the last element.
      This means indicators are being calculated with incomplete
      session data (the close will change as the session progresses).

  Q4: Is daily_change_pct calculated from today's live/partial close
      versus yesterday's close?

  A4: PARTIALLY. daily_change_pct = (latest_close - prev_close) / prev_close * 100
      where latest_close = bars[-1].close (TradingView partial) and
      prev_close = bars[-2].close (Yahoo completed session).
      However, bars[-2] may NOT be yesterday — it's whatever Yahoo's
      last completed bar is. If Yahoo has data up to 2026-07-20 and
      TradingView appends 2026-07-22, then prev_close is from 2026-07-20
      (2 days ago), not 2026-07-21. The gap day (Tuesday) is missing.

  Q5: Can price come from TradingView while volume still comes from Yahoo?

  A5: NO — both come from TradingView. When the TradingView bar is
      appended/updated, ALL fields (Open, High, Low, Close, Volume)
      are overwritten from TradingView data (data_provider.py:162-166
      or 169-179). There is no mixing of OHLC from one provider and
      volume from another WITHIN the same bar.
      However, the BAR BEFORE the latest (bars[-2]) is always from
      Yahoo, so prev_close comes from Yahoo while close comes from
      TradingView — this IS cross-provider mixing at the bar level.

  Q6: Can the radar show Date = today while scoring is actually based
      on the last completed session?

  A6: NO. The radar shows Date = today ONLY when TradingView data is
      available and the latest bar date is today. In that case,
      scoring IS based on today's partial bar. If TradingView is
      unavailable, the radar shows the last Yahoo date and scoring
      uses that completed session. There is no case where the date
      says today but scoring uses a different session's data.

  Q7: Is LCSW's reported Close=33.83, Open=35.00, Volume=0.9x based
      on today's partial session or a completed session?

  A7: TODAY'S PARTIAL SESSION. LCSW's latest bar is 2026-07-22
      (today), sourced from TradingView. The close (33.83), open (35.00),
      and volume (2,062,457) are all from the partial live session.
      The 0.9x RVOL means today's partial volume is 90% of the 20-day
      average full-session volume — which is notable for only ~1 hour
      of trading, suggesting strong selling pressure.
      prev_close = 35.00 is from Yahoo's 2026-07-20 bar (last completed).
      RSI(68.6) and MACD(+0.355) are calculated with the partial bar
      included in the close series.
    """)

    # Save results
    output_path = Path("data/provenance_audit.json")
    output_path.parent.mkdir(exist_ok=True)
    results = []
    for prov in all_provs:
        results.append({
            "ticker": prov.ticker,
            "selected_provider": prov.selected_provider,
            "latest_bar_date": prov.latest_bar_date,
            "latest_bar_is_partial": prov.latest_bar_is_partial,
            "overlay_action": prov.overlay_action,
            "close_source": prov.close_source.source if prov.close_source else "",
            "close_date": prov.close_source.provider_date if prov.close_source else "",
            "volume_source": prov.volume_source.source if prov.volume_source else "",
            "volume_date": prov.volume_source.provider_date if prov.volume_source else "",
            "prev_close_date": prov.previous_close_source.provider_date if prov.previous_close_source else "",
            "consistency_flags": prov.consistency_flags,
        })
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n  Results saved to: {output_path}")


if __name__ == "__main__":
    run_provenance_audit()
