"""
Shadow Provider — Parallel Validation Engine
=============================================

Runs EGXAPI alongside the existing Yahoo Finance provider in the
background.  Produces a CSV comparison log and a summary after
every scan.  NEVER blocks or fails the scan.

Environment
-----------
    DATA_PROVIDER  — "shadow" (default), "fallback", or "egxapi"
    EGXAPI_KEY     — Required for shadow/egxapi modes.

CSV log format
--------------
    logs/provider_validation_YYYYMMDD.csv

Usage
-----
    from providers.shadow import run_shadow_comparison

    summary = run_shadow_comparison(tickers, scan_results)
    print(summary)
"""

from __future__ import annotations

import csv
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from scanner import config
from scanner.data_provider import fetch_live_quote

logger = logging.getLogger("providers.shadow")

LOG_DIR = Path("logs")


# ─── Status constants ────────────────────────────────────────────────
class ShadowStatus:
    MATCH = "MATCH"
    PRICE_DIFF = "PRICE_DIFF"
    VOLUME_DIFF = "VOLUME_DIFF"
    STALE_DATA = "STALE_DATA"
    NO_DATA = "NO_DATA"


# ─── Per-symbol comparison record ────────────────────────────────────
@dataclass
class SymbolComparison:
    timestamp: str
    symbol: str
    provider_price: Optional[float] = None
    egxapi_price: Optional[float] = None
    difference_percent: Optional[float] = None
    provider_volume: Optional[int] = None
    egxapi_volume: Optional[int] = None
    provider_timestamp: Optional[str] = None
    egxapi_timestamp: Optional[str] = None
    provider_high: Optional[float] = None
    egxapi_high: Optional[float] = None
    provider_low: Optional[float] = None
    egxapi_low: Optional[float] = None
    provider_bid: Optional[None] = None
    egxapi_bid: Optional[float] = None
    provider_ask: Optional[None] = None
    egxapi_ask: Optional[float] = None
    status: str = ShadowStatus.NO_DATA

    def to_csv_row(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "provider_price": self.provider_price,
            "egxapi_price": self.egxapi_price,
            "difference_percent": self.difference_percent,
            "provider_volume": self.provider_volume,
            "egxapi_volume": self.egxapi_volume,
            "provider_timestamp": self.provider_timestamp,
            "egxapi_timestamp": self.egxapi_timestamp,
            "status": self.status,
        }


# ─── Summary ─────────────────────────────────────────────────────────
@dataclass
class ShadowSummary:
    timestamp: str = ""
    symbols_compared: int = 0
    matches: int = 0
    price_differences: int = 0
    volume_differences: int = 0
    stale_data: int = 0
    no_data: int = 0
    match_rate_percent: float = 0.0
    records: list[SymbolComparison] = field(default_factory=list)

    def to_text(self) -> str:
        lines = [
            "━━ Shadow Validation Summary ━━",
            f"  Timestamp          : {self.timestamp}",
            f"  Symbols Compared   : {self.symbols_compared}",
            f"  Matches            : {self.matches}",
            f"  Price Differences  : {self.price_differences}",
            f"  Volume Differences : {self.volume_differences}",
            f"  Stale Data         : {self.stale_data}",
            f"  No Data            : {self.no_data}",
            f"  Match Rate         : {self.match_rate_percent:.1f}%",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "symbols_compared": self.symbols_compared,
            "matches": self.matches,
            "price_differences": self.price_differences,
            "volume_differences": self.volume_differences,
            "stale_data": self.stale_data,
            "no_data": self.no_data,
            "match_rate_percent": self.match_rate_percent,
        }


# ─── Core comparison logic ───────────────────────────────────────────
def _compare_one_symbol(symbol: str) -> SymbolComparison:
    """
    Compare Yahoo Finance quote vs EGXAPI quote for a single symbol.

    All exceptions are caught and logged — this never raises.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = SymbolComparison(timestamp=now_str, symbol=symbol)

    try:
        # ── Fetch from Yahoo Finance (existing provider) ──────────────
        yf_quote = fetch_live_quote(symbol)
        record.provider_price = yf_quote.get("last_traded_price")
        record.provider_timestamp = yf_quote.get("quote_time")
        record.provider_high = yf_quote.get("session_high")
        record.provider_low = yf_quote.get("session_low")

        # Volume from Yahoo Finance is not directly in fetch_live_quote,
        # but we can get it from the daily candle in the scan results.
        # For shadow comparison, we leave provider_volume as None here
        # and fill it from scan_results if available.
    except Exception as exc:
        logger.debug("Shadow: Yahoo quote failed for %s: %s", symbol, exc)

    try:
        # ── Fetch from EGXAPI ─────────────────────────────────────────
        from providers.egxapi_provider import get_provider
        egxapi = get_provider()
        egx_quote = egxapi.get_quote(symbol)
        record.egxapi_price = egx_quote.last_price
        record.egxapi_volume = egxapi_quote_volume(egx_quote)
        record.egxapi_timestamp = egx_quote.timestamp
        record.egxapi_high = egx_quote.high
        record.egxapi_low = egx_quote.low
        record.egxapi_bid = egx_quote.bid
        record.egxapi_ask = egx_quote.ask
    except Exception as exc:
        logger.debug("Shadow: EGXAPI quote failed for %s: %s", symbol, exc)

    # ── Determine status ──────────────────────────────────────────────
    record.status = _classify_status(record)
    return record


def egxapi_quote_volume(q) -> Optional[int]:
    """Extract volume from NormalizedQuote."""
    return q.volume


def _classify_status(rec: SymbolComparison) -> str:
    """Classify the comparison status based on thresholds."""
    # No data from either side
    if rec.egxapi_price is None and rec.provider_price is None:
        return ShadowStatus.NO_DATA

    # No EGXAPI data
    if rec.egxapi_price is None:
        return ShadowStatus.NO_DATA

    # No provider data
    if rec.provider_price is None:
        return ShadowStatus.NO_DATA

    # ── Timestamp staleness check ─────────────────────────────────────
    if rec.egxapi_timestamp:
        try:
            ts_dt = datetime.fromisoformat(
                rec.egxapi_timestamp.replace("Z", "+00:00")
            )
            age_sec = (datetime.now(timezone.utc) - ts_dt).total_seconds()
            if age_sec > config.SHADOW_STALE_THRESHOLD_SEC:
                return ShadowStatus.STALE_DATA
        except (ValueError, TypeError):
            pass

    # ── Price difference check ────────────────────────────────────────
    if rec.provider_price != 0:
        diff_pct = abs(
            (rec.egxapi_price - rec.provider_price) / abs(rec.provider_price)
        ) * 100
        rec.difference_percent = round(diff_pct, 4)
    else:
        diff_pct = 0.0 if rec.egxapi_price == 0 else 999.0
        rec.difference_percent = round(diff_pct, 4)

    if diff_pct > config.SHADOW_PRICE_MATCH_THRESHOLD:
        return ShadowStatus.PRICE_DIFF

    # ── Volume difference check ───────────────────────────────────────
    if rec.provider_volume is not None and rec.egxapi_volume is not None:
        if rec.provider_volume != rec.egxapi_volume:
            return ShadowStatus.VOLUME_DIFF

    return ShadowStatus.MATCH


# ─── CSV logging ─────────────────────────────────────────────────────
def _csv_path(date_str: str) -> Path:
    """Return the CSV log path for a given date string (YYYYMMDD)."""
    LOG_DIR.mkdir(exist_ok=True)
    return LOG_DIR / f"provider_validation_{date_str}.csv"


def _append_csv(records: list[SymbolComparison]) -> Path:
    """Append records to today's CSV log.  Creates header if new file."""
    date_str = datetime.now().strftime("%Y%m%d")
    path = _csv_path(date_str)

    fieldnames = [
        "timestamp", "symbol", "provider_price", "egxapi_price",
        "difference_percent", "provider_volume", "egxapi_volume",
        "provider_timestamp", "egxapi_timestamp", "status",
    ]

    file_exists = path.exists() and path.stat().st_size > 0

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for rec in records:
            writer.writerow(rec.to_csv_row())

    logger.info("Shadow CSV appended: %s (%d records)", path, len(records))
    return path


# ─── Public API ──────────────────────────────────────────────────────
def run_shadow_comparison(
    tickers: list[str],
    scan_results: list[dict] | None = None,
    max_workers: int = 8,
) -> ShadowSummary:
    """
    Run shadow comparison for all tickers.

    This function:
      1. Fetches quotes from both Yahoo Finance and EGXAPI for each ticker.
      2. Compares prices, volumes, timestamps.
      3. Logs every comparison to CSV.
      4. Prints a summary.

    Parameters
    ----------
    tickers : list[str]
        Tickers to compare (e.g. ["ARCC", "COMI", ...]).
    scan_results : list[dict] | None
        Optional scan results to extract volume from.
    max_workers : int
        Thread pool size.

    Returns
    -------
    ShadowSummary
    """
    t0 = time.time()

    # Build volume lookup from scan results
    volume_lookup: dict[str, int] = {}
    if scan_results:
        for r in scan_results:
            t = r.get("ticker", "")
            v = r.get("rvol")
            if t and v is not None:
                volume_lookup[t] = r.get("last_volume", 0)

    # ── Parallel comparison ───────────────────────────────────────────
    records: list[SymbolComparison] = []

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_compare_one_symbol, t): t for t in tickers}
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    rec = future.result()
                    # Fill provider_volume from scan results if available
                    if rec.symbol in volume_lookup:
                        rec.provider_volume = volume_lookup[rec.symbol]
                    records.append(rec)
                except Exception as exc:
                    logger.warning("Shadow comparison crashed for %s: %s", sym, exc)
                    # Still record the failure
                    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    records.append(SymbolComparison(
                        timestamp=now_str,
                        symbol=sym,
                        status=ShadowStatus.NO_DATA,
                    ))
    except Exception as exc:
        logger.error("Shadow comparison thread pool failed: %s", exc)

    # ── Sort by symbol ────────────────────────────────────────────────
    records.sort(key=lambda r: r.symbol)

    # ── Compute summary ───────────────────────────────────────────────
    summary = ShadowSummary(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        symbols_compared=len(records),
        matches=sum(1 for r in records if r.status == ShadowStatus.MATCH),
        price_differences=sum(1 for r in records if r.status == ShadowStatus.PRICE_DIFF),
        volume_differences=sum(1 for r in records if r.status == ShadowStatus.VOLUME_DIFF),
        stale_data=sum(1 for r in records if r.status == ShadowStatus.STALE_DATA),
        no_data=sum(1 for r in records if r.status == ShadowStatus.NO_DATA),
        records=records,
    )

    n = summary.symbols_compared
    summary.match_rate_percent = (
        round(summary.matches / n * 100, 1) if n > 0 else 0.0
    )

    # ── Append to CSV ─────────────────────────────────────────────────
    try:
        _append_csv(records)
    except Exception as exc:
        logger.warning("Shadow CSV write failed: %s", exc)

    elapsed = time.time() - t0
    logger.info(
        "Shadow comparison done: %d symbols in %.1fs — match rate %.1f%%",
        summary.symbols_compared, elapsed, summary.match_rate_percent,
    )
    return summary
