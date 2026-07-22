"""
Tests for Data Refresh Pipeline
================================

Tests the Yahoo/TradingView overlay separation, cache TTL behavior,
overlay validation, bot category cache TTL, and concurrency safety.

Usage
-----
    python -m pytest tests/test_data_refresh.py -v
"""

import os
import sys
import time
import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import numpy as np
import pandas as pd
import pytest
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import config
import scanner.data_provider as dp
from scanner.data_provider import (
    _get_cached_yahoo_history,
    _apply_tradingview_overlay,
    _validate_tv_overlay,
    _tv_batch_fetch,
    fetch_history,
    clear_cache,
)

CAIRO_TZ = pytz.timezone("Africa/Cairo")


def _today_cairo():
    return datetime.now(CAIRO_TZ).replace(hour=0, minute=0, second=0, microsecond=0)


# ─── Helpers ──────────────────────────────────────────────────────────
def _make_yahoo_df(
    n=60,
    close_start=50.0,
    volume_base=100000,
    dates=None,
):
    """Create a synthetic Yahoo Finance DataFrame for testing."""
    if dates is None:
        end_dt = _today_cairo()
        dates = pd.date_range(end=end_dt, periods=n, freq="1B")
    closes = [close_start]
    for i in range(1, n):
        closes.append(closes[-1] * 1.002)
    closes = closes[-n:]

    df = pd.DataFrame({
        "Open": [c * 0.998 for c in closes],
        "High": [c * 1.01 for c in closes],
        "Low": [c * 0.99 for c in closes],
        "Close": closes,
        "Volume": [volume_base] * n,
    }, index=dates)
    return df


def _make_tv_data(close=55.0, volume=200000):
    """Create synthetic TradingView data dict."""
    return {
        "close": close,
        "open": close * 0.998,
        "high": close * 1.01,
        "low": close * 0.99,
        "volume": volume,
        "change_pct": 2.0,
        "previous_close": close * 0.98,
    }


# ══════════════════════════════════════════════════════════════════════
# TRADINGVIEW OVERLAY TESTS
# ══════════════════════════════════════════════════════════════════════
class TestTradingViewOverlay:
    """Tests for _apply_tradingview_overlay()."""

    def test_overlay_replaces_today_bar(self):
        """When last bar date == today, overlay updates in-place."""
        today = _today_cairo()
        dates = pd.date_range(end=today, periods=60, freq="1B")
        df = _make_yahoo_df(n=60, dates=dates)

        tv = _make_tv_data(close=75.0, volume=500000)
        result = _apply_tradingview_overlay(df, "TEST", tv_data=tv)

        assert float(result["Close"].iloc[-1]) == 75.0
        assert int(result["Volume"].iloc[-1]) == 500000
        assert result.shape == df.shape

    def test_overlay_appends_new_bar(self):
        """When last bar date != today, overlay appends a new bar."""
        yesterday = _today_cairo() - timedelta(days=1)
        dates = pd.date_range(end=yesterday, periods=60, freq="1B")
        df = _make_yahoo_df(n=60, dates=dates)
        original_len = len(df)

        tv = _make_tv_data(close=80.0, volume=300000)
        result = _apply_tradingview_overlay(df, "TEST", tv_data=tv)

        assert len(result) == original_len + 1
        assert float(result["Close"].iloc[-1]) == 80.0
        assert int(result["Volume"].iloc[-1]) == 300000

    def test_overlay_never_mutates_input(self):
        """Cached Yahoo DataFrame must never be mutated by overlay."""
        today = _today_cairo()
        dates = pd.date_range(end=today, periods=60, freq="1B")
        df = _make_yahoo_df(n=60, dates=dates)
        original_close = float(df["Close"].iloc[-1])
        original_id = id(df)

        tv = _make_tv_data(close=original_close * 1.1, volume=999999)
        result = _apply_tradingview_overlay(df, "TEST", tv_data=tv)

        assert float(df["Close"].iloc[-1]) == original_close
        assert id(df) == original_id
        assert float(result["Close"].iloc[-1]) != original_close

    def test_overlay_no_tv_data_returns_unchanged(self):
        """When no TV data available, returns DataFrame unchanged."""
        today = _today_cairo()
        dates = pd.date_range(end=today, periods=60, freq="1B")
        df = _make_yahoo_df(n=60, dates=dates)
        original_close = float(df["Close"].iloc[-1])

        result = _apply_tradingview_overlay(df, "TEST", tv_data=None)

        assert float(result["Close"].iloc[-1]) == original_close
        assert len(result) == len(df)

    def test_overlay_empty_df_returns_unchanged(self):
        """Empty DataFrame returns unchanged."""
        df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        tv = _make_tv_data(close=55.0)
        result = _apply_tradingview_overlay(df, "TEST", tv_data=tv)
        assert len(result) == 0


# ══════════════════════════════════════════════════════════════════════
# OVERLAY VALIDATION TESTS
# ══════════════════════════════════════════════════════════════════════
class TestOverlayValidation:
    """Tests for _validate_tv_overlay()."""

    def test_valid_overlay_passes(self):
        df = _make_yahoo_df(n=60)
        tv = _make_tv_data(close=55.0, volume=200000)
        assert _validate_tv_overlay(tv, df, "TEST") is True

    def test_zero_close_rejected(self):
        df = _make_yahoo_df(n=60)
        tv = _make_tv_data(close=0.0)
        assert _validate_tv_overlay(tv, df, "TEST") is False

    def test_negative_close_rejected(self):
        df = _make_yahoo_df(n=60)
        tv = _make_tv_data(close=-5.0)
        assert _validate_tv_overlay(tv, df, "TEST") is False

    def test_nan_close_rejected(self):
        df = _make_yahoo_df(n=60)
        tv = _make_tv_data(close=float("nan"))
        assert _validate_tv_overlay(tv, df, "TEST") is False

    def test_none_close_rejected(self):
        df = _make_yahoo_df(n=60)
        tv = {"close": None, "open": 50, "high": 51, "low": 49, "volume": 1000}
        assert _validate_tv_overlay(tv, df, "TEST") is False

    def test_negative_volume_rejected(self):
        df = _make_yahoo_df(n=60)
        tv = _make_tv_data(close=55.0, volume=-100)
        assert _validate_tv_overlay(tv, df, "TEST") is False

    def test_high_less_than_close_rejected(self):
        df = _make_yahoo_df(n=60)
        tv = {
            "close": 55.0,
            "open": 54.0,
            "high": 53.0,
            "low": 52.0,
            "volume": 1000,
        }
        assert _validate_tv_overlay(tv, df, "TEST") is False

    def test_low_greater_than_close_rejected(self):
        df = _make_yahoo_df(n=60)
        tv = {
            "close": 55.0,
            "open": 56.0,
            "high": 57.0,
            "low": 56.5,
            "volume": 1000,
        }
        assert _validate_tv_overlay(tv, df, "TEST") is False

    def test_scale_mismatch_rejected(self):
        """TV close 100x Yahoo avg -> rejected."""
        df = _make_yahoo_df(n=60, close_start=50.0)
        tv = _make_tv_data(close=5000.0, volume=1000)
        assert _validate_tv_overlay(tv, df, "TEST") is False

    def test_scale_mismatch_low_rejected(self):
        """TV close 0.01x Yahoo avg -> rejected."""
        df = _make_yahoo_df(n=60, close_start=50.0)
        tv = _make_tv_data(close=0.1, volume=1000)
        assert _validate_tv_overlay(tv, df, "TEST") is False

    def test_oras_pattern_rejected(self):
        """Zero Yahoo volume + extreme TV value ratio -> rejected."""
        df = _make_yahoo_df(n=60, close_start=50.0, volume_base=0)
        tv = _make_tv_data(close=50.0, volume=1000000)
        assert _validate_tv_overlay(tv, df, "TEST") is False

    def test_valid_close_and_volume_pass(self):
        df = _make_yahoo_df(n=60, close_start=50.0, volume_base=100000)
        tv = _make_tv_data(close=55.0, volume=200000)
        assert _validate_tv_overlay(tv, df, "TEST") is True

    def test_missing_open_defaults_to_close(self):
        """Missing open field gets defaulted to close, then validated."""
        df = _make_yahoo_df(n=60)
        tv = {"close": 55.0, "open": None, "high": 56.0, "low": 54.0, "volume": 1000}
        assert _validate_tv_overlay(tv, df, "TEST") is True
        assert tv["open"] == 55.0

    def test_material_scale_within_bounds(self):
        """TV close within 0.2x-5x of Yahoo avg -> accepted."""
        df = _make_yahoo_df(n=60, close_start=50.0)
        tv = _make_tv_data(close=90.0, volume=1000)
        assert _validate_tv_overlay(tv, df, "TEST") is True


# ══════════════════════════════════════════════════════════════════════
# YAHOO CACHE TESTS
# ══════════════════════════════════════════════════════════════════════
class TestYahooCache:
    """Tests for Yahoo cache behavior."""

    def test_cache_hit_returns_same_data(self):
        """Two rapid calls return same Yahoo data from cache."""
        clear_cache()
        df1 = _make_yahoo_df(n=60)
        with patch("scanner.data_provider.yf") as mock_yf:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = df1
            mock_yf.Ticker.return_value = mock_ticker

            result1, hit1 = _get_cached_yahoo_history("TEST")
            result2, hit2 = _get_cached_yahoo_history("TEST")

        assert hit1 is False
        assert hit2 is True
        assert len(result1) == len(result2)

    def test_cache_expires_after_ttl(self):
        """Cache expires after _CACHE_TTL seconds."""
        clear_cache()
        df1 = _make_yahoo_df(n=60)
        with patch("scanner.data_provider.yf") as mock_yf:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = df1
            mock_yf.Ticker.return_value = mock_ticker

            _get_cached_yahoo_history("TEST")
            dp._CACHE["TEST:1y:1d"] = (dp._CACHE["TEST:1y:1d"][0], time.time() - dp._CACHE_TTL - 1)

            _, hit = _get_cached_yahoo_history("TEST")
        assert hit is False

    def test_cache_never_mutated_by_fetch_history(self):
        """fetch_history returns a deep copy, cache stays intact."""
        clear_cache()
        df = _make_yahoo_df(n=60)
        cache_key = "TEST:1y:1d"
        with patch("scanner.data_provider.yf") as mock_yf:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = df
            mock_yf.Ticker.return_value = mock_ticker
            _get_cached_yahoo_history("TEST")

        cached_df = dp._CACHE[cache_key][0]
        original_close = float(cached_df["Close"].iloc[-1])
        original_id = id(cached_df)

        with patch("scanner.data_provider._is_market_hours", return_value=False):
            result = fetch_history("TEST")

        assert float(cached_df["Close"].iloc[-1]) == original_close
        assert id(cached_df) == original_id
        assert id(result) != original_id


# ══════════════════════════════════════════════════════════════════════
# TRADINGVIEW CACHE BATCH TIMESTAMP TESTS
# ══════════════════════════════════════════════════════════════════════
class TestTradingViewCache:
    """Tests for TradingView batch cache timestamp behavior."""

    def test_batch_timestamp_used_not_first_entry(self):
        """Cache uses _TV_CACHE_TS, not per-entry timestamps."""
        clear_cache()
        dp._TV_CACHE["A"] = {"close": 10}
        dp._TV_CACHE["B"] = {"close": 20}
        dp._TV_CACHE_TS = time.time()

        result = _tv_batch_fetch()
        assert result is dp._TV_CACHE

    def test_cache_expires_after_tv_ttl(self):
        """Cache expires after _TV_CACHE_TTL seconds."""
        clear_cache()
        dp._TV_CACHE["A"] = {"close": 10}
        dp._TV_CACHE_TS = time.time() - dp._TV_CACHE_TTL - 1

        with patch("scanner.data_provider.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"data": []}
            mock_httpx.post.return_value = mock_resp

            _tv_batch_fetch()
            mock_httpx.post.assert_called_once()

    def test_cache_fresh_within_ttl(self):
        """Cache returns within TTL without network call."""
        clear_cache()
        dp._TV_CACHE["A"] = {"close": 10}
        dp._TV_CACHE_TS = time.time()

        with patch("scanner.data_provider.httpx") as mock_httpx:
            result = _tv_batch_fetch()
            mock_httpx.post.assert_not_called()
            assert result["A"]["close"] == 10


# ══════════════════════════════════════════════════════════════════════
# FETCH_HISTORY INTEGRATION TESTS
# ══════════════════════════════════════════════════════════════════════
class TestFetchHistory:
    """Integration tests for fetch_history with overlay separation."""

    def test_market_hours_overlay_applied(self):
        """During market hours, TradingView overlay is applied."""
        clear_cache()
        today = _today_cairo()
        dates = pd.date_range(end=today, periods=60, freq="1B")
        df = _make_yahoo_df(n=60, dates=dates)

        with patch("scanner.data_provider.yf") as mock_yf, \
             patch("scanner.data_provider._is_market_hours", return_value=True), \
             patch("scanner.data_provider._tv_batch_fetch") as mock_tv:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = df
            mock_yf.Ticker.return_value = mock_ticker
            mock_tv.return_value = {"TEST": _make_tv_data(close=75.0)}

            result = fetch_history("TEST")

        assert float(result["Close"].iloc[-1]) == 75.0

    def test_off_hours_no_overlay(self):
        """Outside market hours, no TradingView overlay is applied."""
        clear_cache()
        today = _today_cairo()
        dates = pd.date_range(end=today, periods=60, freq="1B")
        df = _make_yahoo_df(n=60, dates=dates)
        original_close = float(df["Close"].iloc[-1])

        with patch("scanner.data_provider.yf") as mock_yf, \
             patch("scanner.data_provider._is_market_hours", return_value=False):
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = df
            mock_yf.Ticker.return_value = mock_ticker

            result = fetch_history("TEST")

        assert float(result["Close"].iloc[-1]) == original_close

    def test_cache_hit_still_applies_overlay(self):
        """Yahoo cache hit still reapplies TradingView overlay."""
        clear_cache()
        today = _today_cairo()
        dates = pd.date_range(end=today, periods=60, freq="1B")
        df = _make_yahoo_df(n=60, dates=dates)

        with patch("scanner.data_provider.yf") as mock_yf, \
             patch("scanner.data_provider._is_market_hours", return_value=True), \
             patch("scanner.data_provider._tv_batch_fetch") as mock_tv:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = df
            mock_yf.Ticker.return_value = mock_ticker
            mock_tv.return_value = {"TEST": _make_tv_data(close=75.0)}

            result1 = fetch_history("TEST")
            result2 = fetch_history("TEST")

        assert float(result2["Close"].iloc[-1]) == 75.0
        assert mock_yf.Ticker.call_count == 1

    def test_overlay_returns_different_price_after_tv_refresh(self):
        """After TV cache refresh, overlay returns a newer price."""
        clear_cache()
        today = _today_cairo()
        dates = pd.date_range(end=today, periods=60, freq="1B")
        df = _make_yahoo_df(n=60, dates=dates)

        tv_v1 = _make_tv_data(close=75.0)
        tv_v2 = _make_tv_data(close=80.0)

        with patch("scanner.data_provider.yf") as mock_yf, \
             patch("scanner.data_provider._is_market_hours", return_value=True), \
             patch("scanner.data_provider._tv_batch_fetch") as mock_tv:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = df
            mock_yf.Ticker.return_value = mock_ticker

            mock_tv.return_value = {"TEST": tv_v1}
            result1 = fetch_history("TEST")

            mock_tv.return_value = {"TEST": tv_v2}
            result2 = fetch_history("TEST")

        assert float(result1["Close"].iloc[-1]) == 75.0
        assert float(result2["Close"].iloc[-1]) == 80.0


# ══════════════════════════════════════════════════════════════════════
# BOT CATEGORY CACHE TTL TESTS
# ══════════════════════════════════════════════════════════════════════
class TestBotCategoryCache:
    """Tests for bot radar cache TTL behavior."""

    def test_cache_young_reused(self):
        """Cache younger than 300s is reused."""
        import bot as bot_module

        mock_result = MagicMock()
        bot_module._last_radar["result"] = mock_result
        bot_module._last_radar["timestamp"] = datetime.now()

        assert bot_module._radar_cache_is_fresh() is True

    def test_cache_old_triggers_rescan(self):
        """Cache older than 300s triggers fresh scan."""
        import bot as bot_module

        bot_module._last_radar["result"] = MagicMock()
        bot_module._last_radar["timestamp"] = datetime.now() - timedelta(seconds=301)

        assert bot_module._radar_cache_is_fresh() is False

    def test_missing_timestamp_triggers_rescan(self):
        """Missing timestamp triggers fresh scan."""
        import bot as bot_module

        bot_module._last_radar["result"] = MagicMock()
        bot_module._last_radar["timestamp"] = None

        assert bot_module._radar_cache_is_fresh() is False

    def test_missing_result_triggers_rescan(self):
        """Missing result triggers fresh scan."""
        import bot as bot_module

        bot_module._last_radar["result"] = None
        bot_module._last_radar["timestamp"] = datetime.now()

        assert bot_module._radar_cache_is_fresh() is False

    def test_successful_scan_updates_timestamp(self):
        """Successful scan updates both result and timestamp."""
        import bot as bot_module

        mock_result = MagicMock()
        bot_module._update_radar_cache(mock_result)

        assert bot_module._last_radar["result"] is mock_result
        assert bot_module._last_radar["timestamp"] is not None
        age = (datetime.now() - bot_module._last_radar["timestamp"]).total_seconds()
        assert age < 1.0


# ══════════════════════════════════════════════════════════════════════
# CONCURRENCY TESTS
# ══════════════════════════════════════════════════════════════════════
class TestConcurrency:
    """Tests for async lock behavior."""

    def test_radar_lock_exists(self):
        """_radar_lock is an asyncio.Lock."""
        import bot as bot_module
        assert isinstance(bot_module._radar_lock, asyncio.Lock)

    def test_lock_prevents_concurrent_scans(self):
        """Only one scan runs at a time under the lock."""
        import bot as bot_module

        call_count = 0

        async def mock_scan():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return call_count

        async def run_two():
            async with bot_module._radar_lock:
                r1 = await mock_scan()
            async with bot_module._radar_lock:
                r2 = await mock_scan()
            return r1, r2

        r1, r2 = asyncio.run(run_two())
        assert r1 == 1
        assert r2 == 2
        assert call_count == 2


# ══════════════════════════════════════════════════════════════════════
# INTEGRATION TIMING SCENARIO
# ══════════════════════════════════════════════════════════════════════
class TestTimingScenario:
    """Demonstrate the timing scenario from the requirements."""

    def test_t60_tv_price_differs_from_t0(self):
        """T+60s: Yahoo cache valid, TV cache refreshes, overlay B differs from A."""
        clear_cache()
        today = _today_cairo()
        dates = pd.date_range(end=today, periods=60, freq="1B")
        df = _make_yahoo_df(n=60, dates=dates)

        tv_a = _make_tv_data(close=75.0)
        tv_b = _make_tv_data(close=80.0)

        with patch("scanner.data_provider.yf") as mock_yf, \
             patch("scanner.data_provider._is_market_hours", return_value=True), \
             patch("scanner.data_provider._tv_batch_fetch") as mock_tv:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = df
            mock_yf.Ticker.return_value = mock_ticker

            mock_tv.return_value = {"TEST": tv_a}
            result_a = fetch_history("TEST")

            mock_tv.return_value = {"TEST": tv_b}
            result_b = fetch_history("TEST")

        assert float(result_a["Close"].iloc[-1]) == 75.0
        assert float(result_b["Close"].iloc[-1]) == 80.0
        assert float(result_a["Close"].iloc[-1]) != float(result_b["Close"].iloc[-1])

    def test_t300_yahoo_refreshes(self):
        """T+300s: Yahoo history refreshes, TV overlay still applied."""
        clear_cache()
        today = _today_cairo()
        dates = pd.date_range(end=today, periods=60, freq="1B")
        df1 = _make_yahoo_df(n=60, dates=dates, close_start=50.0)
        df2 = _make_yahoo_df(n=60, dates=dates, close_start=55.0)

        tv = _make_tv_data(close=60.0)

        with patch("scanner.data_provider.yf") as mock_yf, \
             patch("scanner.data_provider._is_market_hours", return_value=True), \
             patch("scanner.data_provider._tv_batch_fetch") as mock_tv:
            mock_ticker = MagicMock()
            mock_ticker.history.side_effect = [df1, df2]
            mock_yf.Ticker.return_value = mock_ticker
            mock_tv.return_value = {"TEST": tv}

            result1 = fetch_history("TEST")
            dp._CACHE.clear()
            result2 = fetch_history("TEST")

        assert float(result1["Close"].iloc[-1]) == 60.0
        assert float(result2["Close"].iloc[-1]) == 60.0
        assert mock_yf.Ticker.call_count == 2


# ══════════════════════════════════════════════════════════════════════
# CLEAR CACHE TEST
# ══════════════════════════════════════════════════════════════════════
class TestClearCache:
    """Tests for clear_cache()."""

    def test_clear_cache_empties_both_caches(self):
        """clear_cache() empties Yahoo and TradingView caches."""
        dp._CACHE["x"] = (pd.DataFrame(), 0)
        dp._TV_CACHE["y"] = {"close": 10}
        dp._TV_CACHE_TS = time.time()

        clear_cache()

        assert len(dp._CACHE) == 0
        assert len(dp._TV_CACHE) == 0
        assert dp._TV_CACHE_TS == 0.0
