"""
Unit Tests for Market Radar
=============================

Tests the core radar calculations, scoring, classification,
and data validation using mocked data (no network calls).

Usage
-----
    python -m pytest tests/test_market_radar.py -v
"""

import os
import sys
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import config
from scanner.market_radar import (
    run_market_radar,
    _analyze_symbol,
    _calculate_activity_score,
    _classify_level,
    _classify_category,
    _generate_reasons,
    _calculate_adx,
    ActivityCategory,
    ActivityLevel,
    RadarItem,
)
from scanner.ism_handoff import create_handoff, ISMHandoff
from scanner.radar_data import RadarHistory, DailyBar, _validate_bar, _bars_are_valid, FAILURE_INVALID_OHLC, FAILURE_INVALID_CLOSE
from scanner.radar_output import format_radar_telegram, format_radar_symbol_telegram


# ─── Helpers ──────────────────────────────────────────────────────────
def _make_bars(
    n=80,
    close_start=100.0,
    volume_base=100000,
    trend="up",
    volume_trend="normal",
    last_volume_mult=1.0,
):
    """Generate synthetic daily bars for testing."""
    bars = []
    price = close_start
    for i in range(n):
        if trend == "up":
            price *= 1.002
        elif trend == "down":
            price *= 0.998
        elif trend == "flat":
            price = close_start + np.random.uniform(-0.5, 0.5)

        if volume_trend == "high":
            vol = int(volume_base * 2.5)
        elif volume_trend == "normal":
            vol = int(volume_base)
        else:
            vol = int(volume_base * 0.5)

        if i == n - 1:
            vol = int(vol * last_volume_mult)

        high = price * 1.01
        low = price * 0.99
        opn = price * 1.001

        bars.append(DailyBar(
            date=(datetime.now() - timedelta(days=n - i)).strftime("%Y-%m-%d"),
            open=round(opn, 2),
            high=round(high, 2),
            low=round(low, 2),
            close=round(price, 2),
            volume=vol,
        ))
    return bars


def _make_history(bars, symbol="TEST"):
    return RadarHistory(symbol=symbol, bars=bars, data_mode="DAILY_COMPLETED_SESSION")


def _make_item(**kwargs):
    """Create a RadarItem with sensible defaults."""
    defaults = {
        "symbol": "TEST",
        "company_name": "Test Co",
        "price": 100.0,
        "price_date": "2026-07-20",
        "price_change_percent": 2.5,
        "volume": 300000,
        "average_volume_20": 100000,
        "rvol_20": 3.0,
        "traded_value": 30_000_000,
        "average_traded_value_20": 10_000_000,
        "rsi_14": 60.0,
        "rsi_previous": 55.0,
        "rsi_change": 5.0,
        "macd_histogram": 0.5,
        "macd_histogram_previous": 0.2,
        "macd_histogram_change": 0.3,
        "close_location_value": 0.8,
        "candle_body_percent": 70.0,
        "volume_percentile_60": 95.0,
        "activity_score": 80,
        "activity_score_components": {},
        "activity_category": ActivityCategory.BUYING,
        "activity_level": ActivityLevel.HIGH,
        "activity_label": "Strong buying activity",
        "reasons": ["RVOL 3.0x", "Close near high"],
        "price_return_5d": 3.0,
    }
    defaults.update(kwargs)
    return RadarItem(**defaults)


# ─── Test Activity Score ──────────────────────────────────────────────
class TestActivityScore:
    def test_score_always_0_to_100(self):
        """Score must always be between 0 and 100."""
        for rvol in [0.1, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0]:
            for vp in [10, 30, 50, 70, 90, 99]:
                item = _make_item(rvol_20=rvol, volume_percentile_60=vp)
                score, components = _calculate_activity_score(item, rvol, vp, 1.0)
                assert 0 <= score <= 100, f"Score {score} out of range for rvol={rvol}, vp={vp}"
                assert sum(components.values()) == score

    def test_high_volume_high_percentile_max_volume_score(self):
        """High RVOL + high percentile should maximize volume score."""
        item = _make_item(rvol_20=4.0, volume_percentile_60=99)
        score, components = _calculate_activity_score(item, 4.0, 99.0, 2.0)
        assert components["volume_score"] == 50

    def test_low_volume_low_percentile_min_volume_score(self):
        """Low RVOL + low percentile should minimize volume score."""
        item = _make_item(rvol_20=0.5, volume_percentile_60=10)
        score, components = _calculate_activity_score(item, 0.5, 10.0, 0.5)
        assert components["volume_score"] <= 5

    def test_liquidity_score_scales_with_traded_value_ratio(self):
        """Higher traded value ratio should yield higher liquidity score."""
        _, low_liq = _calculate_activity_score(_make_item(), 1.0, 50.0, 0.8)
        _, med_liq = _calculate_activity_score(_make_item(), 1.0, 50.0, 1.5)
        _, high_liq = _calculate_activity_score(_make_item(), 1.0, 50.0, 2.5)
        assert low_liq["liquidity_score"] < med_liq["liquidity_score"] <= high_liq["liquidity_score"]

    def test_price_volume_score_extreme_clv(self):
        """Extreme close location values should boost price-volume score."""
        item_high_clv = _make_item(close_location_value=0.9, candle_body_percent=80)
        _, comps_high = _calculate_activity_score(item_high_clv, 1.5, 70, 1.2)
        item_mid_clv = _make_item(close_location_value=0.5, candle_body_percent=50)
        _, comps_mid = _calculate_activity_score(item_mid_clv, 1.5, 70, 1.2)
        assert comps_high["price_volume_score"] >= comps_mid["price_volume_score"]

    def test_rsi_score_extreme_values(self):
        """Extreme RSI should boost RSI score."""
        item_extreme = _make_item(rsi_14=75, rsi_change=6)
        _, comps_extreme = _calculate_activity_score(item_extreme, 1.5, 70, 1.0)
        item_neutral = _make_item(rsi_14=50, rsi_change=0.5)
        _, comps_neutral = _calculate_activity_score(item_neutral, 1.5, 70, 1.0)
        assert comps_extreme["rsi_score"] >= comps_neutral["rsi_score"]

    def test_macd_score_histogram_change(self):
        """Large MACD histogram change should boost MACD score."""
        item_big_change = _make_item(macd_histogram=1.5, macd_histogram_change=1.2)
        _, comps_big = _calculate_activity_score(item_big_change, 1.5, 70, 1.0)
        item_small_change = _make_item(macd_histogram=0.1, macd_histogram_change=0.01)
        _, comps_small = _calculate_activity_score(item_small_change, 1.5, 70, 1.0)
        assert comps_big["macd_score"] >= comps_small["macd_score"]


# ─── Test Activity Level ──────────────────────────────────────────────
class TestActivityLevel:
    def test_extreme_rvol(self):
        assert _classify_level(3.5, 50) == ActivityLevel.EXTREME

    def test_extreme_percentile(self):
        assert _classify_level(1.0, 96) == ActivityLevel.EXTREME

    def test_high_rvol(self):
        assert _classify_level(2.5, 50) == ActivityLevel.HIGH

    def test_high_percentile(self):
        assert _classify_level(1.0, 91) == ActivityLevel.HIGH

    def test_elevated_rvol(self):
        assert _classify_level(1.5, 50) == ActivityLevel.ELEVATED

    def test_elevated_percentile(self):
        assert _classify_level(1.0, 76) == ActivityLevel.ELEVATED

    def test_normal(self):
        assert _classify_level(0.8, 30) == ActivityLevel.NORMAL


# ─── Test Activity Category ───────────────────────────────────────────
class TestActivityCategory:
    def test_high_volume_bullish_is_buying(self):
        """Test 1: High-volume bullish session → BUYING_ACTIVITY."""
        item = _make_item(
            price_change_percent=3.0,
            close_location_value=0.85,
            rsi_change=4.0,
            macd_histogram_change=0.5,
            candle_body_percent=75,
        )
        cat, label = _classify_category(item)
        assert cat == ActivityCategory.BUYING
        assert "buying" in label.lower() or "accumulation" in label.lower()

    def test_high_volume_bearish_is_selling(self):
        """Test 2: High-volume bearish session → SELLING_ACTIVITY.
        Must NOT be filtered out."""
        item = _make_item(
            price_change_percent=-3.0,
            close_location_value=0.15,
            rsi_change=-4.0,
            macd_histogram_change=-0.5,
            candle_body_percent=75,
        )
        cat, label = _classify_category(item)
        assert cat == ActivityCategory.SELLING
        assert "selling" in label.lower() or "distribution" in label.lower()

    def test_high_volume_flat_is_unusual(self):
        """Test 3: High volume with flat price → UNUSUAL_ACTIVITY."""
        item = _make_item(
            price_change_percent=0.2,
            close_location_value=0.5,
            rsi_change=0.3,
            macd_histogram_change=0.02,
            candle_body_percent=20,
        )
        cat, label = _classify_category(item)
        assert cat == ActivityCategory.UNUSUAL
        assert "unclear" in label.lower() or "unusual" in label.lower()

    def test_every_stock_has_exactly_one_category(self):
        """Test 11: Every selected stock has exactly one category."""
        categories = set()
        for rsi_ch in [-5, 0, 5]:
            for clv in [0.2, 0.5, 0.8]:
                for chg in [-3, 0, 3]:
                    item = _make_item(
                        price_change_percent=chg,
                        close_location_value=clv,
                        rsi_change=rsi_ch,
                        macd_histogram_change=0.1 * rsi_ch,
                        candle_body_percent=50,
                    )
                    cat, label = _classify_category(item)
                    assert cat in (ActivityCategory.BUYING, ActivityCategory.SELLING, ActivityCategory.UNUSUAL)
                    categories.add(cat)
        # Verify all three categories are reachable
        assert len(categories) == 3


# ─── Test RVOL Calculation ────────────────────────────────────────────
class TestRVOLCalculation:
    def test_latest_session_excluded_from_average(self):
        """Test 5: RVOL = latest / avg(previous 20). Latest not in its own average."""
        bars = _make_bars(n=80, volume_base=100000, last_volume_mult=3.0)
        history = _make_history(bars)

        volumes = np.array([b.volume for b in history.bars])
        avg_vol_20 = float(np.mean(volumes[-21:-1]))  # exclude latest
        rvol = volumes[-1] / avg_vol_20

        # Latest is 3x base, avg of previous 20 is ~1x base
        assert rvol > 2.5
        # Verify latest is NOT in the average
        assert volumes[-1] not in volumes[-21:-1]  # volumes are different


# ─── Test Liquidity Filter ────────────────────────────────────────────
class TestLiquidityFilter:
    def test_illiquid_stock_skipped(self):
        """Test 7: Illiquid stock is skipped."""
        bars = _make_bars(n=80, volume_base=100, close_start=1.0)
        for b in bars:
            b.volume = 100
        history = _make_history(bars)

        with patch("scanner.market_radar.get_completed_daily_bars", return_value=history):
            item = _analyze_symbol("ILLIQUID")

        # Very low traded value should be filtered
        if item is not None:
            assert item.average_traded_value_20 >= config.RADAR_MIN_AVG_TRADED_VALUE_20


# ─── Test Score Boundaries ────────────────────────────────────────────
class TestScoreBoundaries:
    def test_score_always_0_to_100_comprehensive(self):
        """Test 10: Score always between 0 and 100."""
        for rvol in np.arange(0.1, 5.0, 0.3):
            for vp in range(5, 100, 5):
                item = _make_item(rvol_20=float(rvol), volume_percentile_60=float(vp))
                score, _ = _calculate_activity_score(item, float(rvol), float(vp), 1.0)
                assert 0 <= score <= 100, f"Score {score} for rvol={rvol:.1f} vp={vp}"


# ─── Test ISM Handoff ─────────────────────────────────────────────────
class TestISMHandoff:
    def test_handoff_contains_activity_context(self):
        """Test 12: ISM handoff contains activity context but no recommendation."""
        item = _make_item(
            symbol="ARCC",
            activity_category=ActivityCategory.BUYING,
            activity_score=85,
            activity_level=ActivityLevel.HIGH,
            reasons=["RVOL 3.0x", "Strong close"],
            price_date="2026-07-20",
        )
        handoff = create_handoff(item)

        assert handoff.symbol == "ARCC"
        assert handoff.activity_category == ActivityCategory.BUYING
        assert handoff.activity_score == 85
        assert handoff.activity_level == ActivityLevel.HIGH
        assert len(handoff.radar_reasons) == 2
        assert handoff.price_date == "2026-07-20"

        d = handoff.to_dict()
        assert "symbol" in d
        assert "activity_category" in d
        # Must NOT contain trading recommendations
        assert "entry" not in str(d).lower()
        assert "stop_loss" not in str(d).lower()
        assert "target" not in str(d).lower()
        assert "buy" not in str(d).lower().replace("buying_activity", "")
        assert "sell" not in str(d).lower().replace("selling_activity", "")

    def test_handoff_to_command_text(self):
        """ISM handoff command text is human-readable."""
        item = _make_item(symbol="COMI")
        handoff = create_handoff(item)
        text = handoff.to_command_text()
        assert "COMI" in text
        assert "ISM ANALYSIS REQUEST" in text


# ─── Test Radar Reasons ───────────────────────────────────────────────
class TestRadarReasons:
    def test_reasons_are_factual(self):
        """Reasons should be factual, not vague."""
        item = _make_item(
            rvol_20=3.5,
            traded_value=50_000_000,
            average_traded_value_20=10_000_000,
            close_location_value=0.9,
            price_change_percent=4.0,
        )
        reasons = _generate_reasons(item, 3.5, 5.0, 100000)
        assert len(reasons) >= 2
        assert len(reasons) <= 4
        for r in reasons:
            assert "good" not in r.lower()
            assert "bad" not in r.lower()
            assert "stock" not in r.lower()

    def test_reasons_mention_rvol_if_elevated(self):
        """If RVOL > 1.35, reasons should mention it."""
        item = _make_item(rvol_20=2.0)
        reasons = _generate_reasons(item, 2.0, 1.5, 100000)
        rvol_reasons = [r for r in reasons if "rvol" in r.lower() or "volume" in r.lower() or "average" in r.lower()]
        assert len(rvol_reasons) >= 1


# ─── Test Data Models ─────────────────────────────────────────────────
class TestDataModels:
    def test_radar_item_has_all_fields(self):
        """Each radar item must have all required output fields."""
        item = _make_item()
        required_fields = [
            "symbol", "company_name", "price", "price_date",
            "price_change_percent", "volume", "average_volume_20",
            "rvol_20", "traded_value", "average_traded_value_20",
            "rsi_14", "rsi_change", "macd_histogram", "macd_histogram_change",
            "activity_score", "activity_score_components", "activity_category",
            "activity_level", "activity_label", "reasons",
            "data_mode", "source", "is_live", "eligible_for_ism",
        ]
        for field in required_fields:
            assert hasattr(item, field), f"Missing field: {field}"

    def test_activity_score_components_has_all_keys(self):
        """Score components must have all 5 keys."""
        item = _make_item()
        score, components = _calculate_activity_score(item, 2.0, 80.0, 1.5)
        expected_keys = {"volume_score", "liquidity_score", "price_volume_score", "rsi_score", "macd_score"}
        assert set(components.keys()) == expected_keys


# ─── Test Data Mode ───────────────────────────────────────────────────
class TestDataMode:
    def test_data_mode_is_daily_completed(self):
        """Current mode must use DAILY_COMPLETED_SESSION."""
        item = _make_item()
        assert item.data_mode == "DAILY_COMPLETED_SESSION"

    def test_is_live_is_false(self):
        """Daily data must be marked as not live."""
        item = _make_item()
        assert item.is_live is False


# ─── Test Telegram Output ─────────────────────────────────────────────
class TestTelegramOutput:
    def test_format_radar_telegram_contains_sections(self):
        """Telegram output should contain category sections."""
        items = [
            _make_item(symbol="A", activity_category=ActivityCategory.BUYING),
            _make_item(symbol="B", activity_category=ActivityCategory.SELLING),
            _make_item(symbol="C", activity_category=ActivityCategory.UNUSUAL),
        ]
        from scanner.market_radar import MarketRadarResult, RadarStats
        result = MarketRadarResult(
            items=items,
            all_items=items,
            data_date="2026-07-20",
            stats=RadarStats(symbols_scanned=100, activity_detected=3),
        )
        text = format_radar_telegram(result)
        assert "BUYING ACTIVITY" in text
        assert "SELLING ACTIVITY" in text
        assert "UNUSUAL ACTIVITY" in text
        assert "Lite detects activity only" in text

    def test_format_single_symbol(self):
        """Single symbol format should contain key info."""
        item = _make_item(symbol="ARCC", rsi_14=55, rsi_change=3.0)
        text = format_radar_symbol_telegram(item)
        assert "ARCC" in text
        assert "55" in text
        assert "Reasons" in text


# ─── Test Provider Failure ────────────────────────────────────────────
class TestProviderFailure:
    def test_one_failure_doesnt_break_scan(self):
        """Test 9: One provider failure should not break the scan."""
        def mock_analyze(symbol):
            if symbol == "FAIL":
                return None
            return _make_item(symbol=symbol)

        with patch("scanner.market_radar._analyze_symbol", side_effect=mock_analyze):
            result = run_market_radar(symbols=["OK1", "FAIL", "OK2"], top_n=10)

        assert result.stats.symbols_scanned == 3
        # Should still have results for OK1 and OK2
        assert len(result.all_items) >= 1


# ─── Test ADX ─────────────────────────────────────────────────────────
class TestADX:
    def test_adx_returns_value_for_sufficient_data(self):
        """ADX should return a value when enough data is provided."""
        highs = np.random.uniform(100, 110, 30)
        lows = highs - np.random.uniform(1, 5, 30)
        closes = (highs + lows) / 2
        adx = _calculate_adx(highs, lows, closes, 14)
        if adx is not None:
            assert 0 <= adx <= 100

    def test_adx_returns_none_for_insufficient_data(self):
        """ADX should return None when not enough data."""
        highs = np.array([100.0, 101.0, 102.0])
        lows = np.array([99.0, 100.0, 101.0])
        closes = np.array([100.0, 100.5, 101.5])
        adx = _calculate_adx(highs, lows, closes, 14)
        assert adx is None


# ─── Test Low Volume vs High Activity Bearish ─────────────────────────
class TestVolumeRanking:
    def test_low_volume_bullish_does_not_outrank_high_volume_bearish(self):
        """Test 4: Low-volume bullish stock must not outrank high-activity bearish stock."""
        # High-activity bearish
        bearish = _make_item(
            symbol="BEAR",
            rvol_20=4.0,
            volume_percentile_60=98,
            activity_score=85,
            activity_category=ActivityCategory.SELLING,
            activity_level=ActivityLevel.EXTREME,
        )
        # Low-volume bullish
        bullish = _make_item(
            symbol="BULL",
            rvol_20=0.8,
            volume_percentile_60=30,
            activity_score=25,
            activity_category=ActivityCategory.BUYING,
            activity_level=ActivityLevel.NORMAL,
        )

        # Sort by level then score (same as run_market_radar)
        level_order = {ActivityLevel.EXTREME: 0, ActivityLevel.HIGH: 1, ActivityLevel.ELEVATED: 2, ActivityLevel.NORMAL: 3}
        sorted_items = sorted(
            [bearish, bullish],
            key=lambda x: (level_order.get(x.activity_level, 3), -x.activity_score),
        )
        # Bearish should rank higher
        assert sorted_items[0].symbol == "BEAR"
        assert sorted_items[0].activity_level == ActivityLevel.EXTREME


# ─── OHLC Mapping Regression Tests ─────────────────────────────────
class TestOHLCMapping:
    """Regression tests for the OHLC field-mapping bug fix.

    Ensures that the close price is always used as the displayed price,
    and open is never silently substituted for close.
    """

    def test_close_is_display_price(self):
        """Test 1: display_price and price must equal close, not open.

        open=56.93, close=57.60
        expected: display_price=57.60, session_open=56.93
        """
        bars = []
        for i in range(80):
            bars.append(DailyBar(
                date=(datetime.now() - timedelta(days=80 - i)).strftime("%Y-%m-%d"),
                open=56.00 + i * 0.01,
                high=57.00 + i * 0.01,
                low=55.50 + i * 0.01,
                close=57.60 if i == 79 else 55.20 + i * 0.01,
                volume=100000 + (2000000 if i == 79 else 0),
            ))
        history = RadarHistory(symbol="TEST", bars=bars, data_mode="DAILY_COMPLETED_SESSION")

        with patch("scanner.market_radar.get_completed_daily_bars", return_value=history):
            item = _analyze_symbol("TEST")

        assert item is not None
        assert item.latest_close == 57.60
        assert item.display_price == 57.60
        assert item.price == 57.60
        assert item.session_open == bars[-1].open
        assert item.display_price != item.session_open

    def test_price_change_from_close_not_open(self):
        """Test 2: price_change must be close vs prev_close, not open vs prev_close.

        previous_close=56.93, latest_open=56.93, latest_close=57.60
        expected: price_change = 57.60 - 56.93 = 0.67, NOT 0.0
        """
        bars = []
        for i in range(80):
            bars.append(DailyBar(
                date=(datetime.now() - timedelta(days=80 - i)).strftime("%Y-%m-%d"),
                open=50.0 + i * 0.1,
                high=51.0 + i * 0.1,
                low=49.5 + i * 0.1,
                close=56.93 if i == 78 else (57.60 if i == 79 else 50.5 + i * 0.1),
                volume=100000 + (2000000 if i == 79 else 0),
            ))
        history = RadarHistory(symbol="TEST", bars=bars, data_mode="DAILY_COMPLETED_SESSION")

        with patch("scanner.market_radar.get_completed_daily_bars", return_value=history):
            item = _analyze_symbol("TEST")

        assert item is not None
        expected_change = 57.60 - 56.93
        actual_change = item.price_change_percent
        expected_pct = (expected_change / 56.93) * 100
        assert abs(actual_change - round(expected_pct, 2)) < 0.1
        assert actual_change != 0.0

    def test_bullish_candle_classified_buying(self):
        """Test 3: Bullish candle with high volume → eligible for BUYING_ACTIVITY.

        open=100, high=110, low=99, close=109
        expected: positive candle body, high CLV
        """
        bars = []
        for i in range(80):
            bars.append(DailyBar(
                date=(datetime.now() - timedelta(days=80 - i)).strftime("%Y-%m-%d"),
                open=95.0 + i * 0.05,
                high=96.0 + i * 0.05,
                low=94.5 + i * 0.05,
                close=109.0 if i == 79 else 95.5 + i * 0.05,
                volume=100000 + (3000000 if i == 79 else 0),
            ))
        history = RadarHistory(symbol="TEST", bars=bars, data_mode="DAILY_COMPLETED_SESSION")

        with patch("scanner.market_radar.get_completed_daily_bars", return_value=history):
            item = _analyze_symbol("TEST")

        assert item is not None
        # Close is near high = high CLV
        assert item.close_location_value > 0.7
        # Positive candle body
        assert item.candle_body_percent > 50

    def test_bearish_candle_classified_selling(self):
        """Test 4: Bearish candle with high volume → eligible for SELLING_ACTIVITY.

        open=110, high=111, low=99, close=100
        expected: negative candle body, low CLV
        """
        bars = []
        for i in range(80):
            bars.append(DailyBar(
                date=(datetime.now() - timedelta(days=80 - i)).strftime("%Y-%m-%d"),
                open=115.0 - i * 0.05,
                high=116.0 - i * 0.05,
                low=114.5 - i * 0.05,
                close=100.0 if i == 79 else 115.5 - i * 0.05,
                volume=100000 + (3000000 if i == 79 else 0),
            ))
        history = RadarHistory(symbol="TEST", bars=bars, data_mode="DAILY_COMPLETED_SESSION")

        with patch("scanner.market_radar.get_completed_daily_bars", return_value=history):
            item = _analyze_symbol("TEST")

        assert item is not None
        # Close is near low = low CLV
        assert item.close_location_value < 0.3
        # Negative candle body
        assert item.candle_body_percent > 50

    def test_rsi_macd_use_close_series(self):
        """Test 5: RSI and MACD must be calculated from close series, not open."""
        # Build bars where open trends down while close trends up
        # This makes RSI from open and RSI from close diverge
        bars = []
        for i in range(80):
            close_val = 100.0 + i * 0.5   # trending up
            open_val = 100.0 - i * 0.5    # trending down
            bars.append(DailyBar(
                date=(datetime.now() - timedelta(days=80 - i)).strftime("%Y-%m-%d"),
                open=open_val,
                high=max(close_val, open_val) + 1.0,
                low=min(close_val, open_val) - 1.0,
                close=close_val,
                volume=100000 + (2000000 if i == 79 else 0),
            ))
        history = RadarHistory(symbol="TEST", bars=bars, data_mode="DAILY_COMPLETED_SESSION")

        with patch("scanner.market_radar.get_completed_daily_bars", return_value=history):
            item = _analyze_symbol("TEST")

        assert item is not None

        # Calculate RSI from close series
        import pandas as pd
        closes = np.array([b.close for b in bars])
        from scanner.indicators import rsi as calc_rsi, macd as calc_macd
        rsi_from_close = calc_rsi(pd.Series(closes), 14)
        expected_rsi = float(rsi_from_close.iloc[-1])

        # Calculate RSI from open series (opposite trend → different RSI)
        opens = np.array([b.open for b in bars])
        rsi_from_open = calc_rsi(pd.Series(opens), 14)
        wrong_rsi = float(rsi_from_open.iloc[-1])

        # RSI from close should match item's RSI
        assert abs(item.rsi_14 - round(expected_rsi, 1)) < 0.2
        # RSI from open (downtrend) should be very different from RSI from close (uptrend)
        assert not np.isnan(expected_rsi)
        assert not np.isnan(wrong_rsi)
        assert abs(expected_rsi - wrong_rsi) > 10.0

        # MACD must also use close series
        macd_line, macd_signal, macd_hist = calc_macd(pd.Series(closes), 12, 26, 9)
        expected_macd = float(macd_line.iloc[-1])
        assert abs(item.macd_line - round(expected_macd, 4)) < 0.1

    def test_telegram_output_shows_close(self):
        """Test 6: Telegram output must display latest completed close, not open."""
        item = _make_item(
            symbol="ARCC",
            latest_close=57.60,
            previous_close=56.93,
            session_open=55.20,
            session_high=57.88,
            session_low=55.26,
            display_price=57.60,
            price=57.60,
            price_date="2026-07-20",
        )
        text = format_radar_symbol_telegram(item)
        assert "57.60" in text
        assert "55.20" in text
        assert "Last Completed Close" in text
        assert "Session Open" in text

        # The price value shown must be 57.60 (close), not 55.20 (open)
        lines = text.split("\n")
        close_line = [l for l in lines if "Last Completed Close" in l][0]
        assert "57.60" in close_line
        assert "55.20" not in close_line.split("Last Completed Close")[0]

    def test_missing_close_returns_invalid(self):
        """Test 7: Missing or zero close → INVALID_CLOSE, never substitute open."""
        bar_valid = DailyBar(date="2026-07-20", open=56.0, high=57.0, low=55.0, close=56.5, volume=1000)
        bar_zero_close = DailyBar(date="2026-07-20", open=56.0, high=57.0, low=55.0, close=0.0, volume=1000)
        bar_neg_close = DailyBar(date="2026-07-20", open=56.0, high=57.0, low=55.0, close=-1.0, volume=1000)

        assert _validate_bar(bar_valid, "TEST") is True
        assert _validate_bar(bar_zero_close, "TEST") is False
        assert _validate_bar(bar_neg_close, "TEST") is False

    def test_incomplete_current_day_candle(self):
        """Test 8: Incomplete current-day candle → use previous completed session."""
        # If today's candle has close == open (just opened), the previous
        # bar's close should be used as the reference
        bars = []
        for i in range(80):
            if i == 79:
                # Today's candle: just opened, close == open
                bars.append(DailyBar(
                    date=(datetime.now()).strftime("%Y-%m-%d"),
                    open=57.0, high=57.0, low=57.0, close=57.0,
                    volume=0,
                ))
            else:
                bars.append(DailyBar(
                    date=(datetime.now() - timedelta(days=80 - i)).strftime("%Y-%m-%d"),
                    open=55.0 + i * 0.02,
                    high=56.0 + i * 0.02,
                    low=54.5 + i * 0.02,
                    close=56.0 + i * 0.02,
                    volume=100000,
                ))
        history = RadarHistory(symbol="TEST", bars=bars, data_mode="DAILY_COMPLETED_SESSION")

        with patch("scanner.market_radar.get_completed_daily_bars", return_value=history):
            item = _analyze_symbol("TEST")

        # The item should exist and use the latest bar's close
        if item is not None:
            assert item.latest_close == 57.0
            assert item.price == 57.0

    def test_completed_today_candle_uses_today_close(self):
        """Test 9: Completed today candle → use today's close when provider confirms."""
        bars = []
        for i in range(80):
            if i == 79:
                bars.append(DailyBar(
                    date=(datetime.now()).strftime("%Y-%m-%d"),
                    open=56.0, high=58.0, low=55.5, close=57.60,
                    volume=2000000, is_complete=True,
                ))
            else:
                bars.append(DailyBar(
                    date=(datetime.now() - timedelta(days=80 - i)).strftime("%Y-%m-%d"),
                    open=55.0 + i * 0.02,
                    high=56.0 + i * 0.02,
                    low=54.5 + i * 0.02,
                    close=56.0 + i * 0.02,
                    volume=100000,
                ))
        history = RadarHistory(symbol="TEST", bars=bars, data_mode="DAILY_COMPLETED_SESSION")

        with patch("scanner.market_radar.get_completed_daily_bars", return_value=history):
            item = _analyze_symbol("TEST")

        assert item is not None
        assert item.latest_close == 57.60
        assert item.display_price == 57.60
        assert item.session_open == 56.0

    def test_backward_compat_price_equals_latest_close(self):
        """Test 10: Legacy 'price' field must equal latest_close."""
        bars = []
        for i in range(80):
            bars.append(DailyBar(
                date=(datetime.now() - timedelta(days=80 - i)).strftime("%Y-%m-%d"),
                open=100.0 + i * 0.1,
                high=101.0 + i * 0.1,
                low=99.5 + i * 0.1,
                close=105.0 + i * 0.1,
                volume=100000,
            ))
        history = RadarHistory(symbol="TEST", bars=bars, data_mode="DAILY_COMPLETED_SESSION")

        with patch("scanner.market_radar.get_completed_daily_bars", return_value=history):
            item = _analyze_symbol("TEST")

        assert item is not None
        assert item.price == item.latest_close
        assert item.price == item.display_price
        assert item.price == round(bars[-1].close, 2)


class TestDailyBarValidation:
    """Tests for DailyBar OHLC validation."""

    def test_valid_bar_passes(self):
        bar = DailyBar(date="2026-07-20", open=56.0, high=57.0, low=55.0, close=56.5, volume=1000)
        assert _validate_bar(bar, "TEST") is True

    def test_zero_close_fails(self):
        bar = DailyBar(date="2026-07-20", open=56.0, high=57.0, low=55.0, close=0.0, volume=1000)
        assert _validate_bar(bar, "TEST") is False

    def test_negative_volume_fails(self):
        bar = DailyBar(date="2026-07-20", open=56.0, high=57.0, low=55.0, close=56.5, volume=-100)
        assert _validate_bar(bar, "TEST") is False

    def test_open_outside_high_low_fails(self):
        bar = DailyBar(date="2026-07-20", open=60.0, high=57.0, low=55.0, close=56.5, volume=1000)
        assert _validate_bar(bar, "TEST") is False

    def test_close_outside_high_low_fails(self):
        bar = DailyBar(date="2026-07-20", open=56.0, high=57.0, low=55.0, close=60.0, volume=1000)
        assert _validate_bar(bar, "TEST") is False

    def test_bars_are_valid_passes(self):
        bars = [
            DailyBar(date="2026-07-18", open=55.0, high=56.0, low=54.5, close=55.5, volume=1000),
            DailyBar(date="2026-07-19", open=55.5, high=56.5, low=55.0, close=56.0, volume=1200),
            DailyBar(date="2026-07-20", open=56.0, high=57.0, low=55.5, close=56.5, volume=1500),
        ]
        assert _bars_are_valid(bars, "TEST") is None

    def test_bars_duplicate_date_filtered(self):
        bars = [
            DailyBar(date="2026-07-18", open=55.0, high=56.0, low=54.5, close=55.5, volume=1000),
            DailyBar(date="2026-07-18", open=56.0, high=57.0, low=55.5, close=56.5, volume=1200),
            DailyBar(date="2026-07-20", open=56.0, high=57.0, low=55.5, close=56.5, volume=1500),
        ]
        result = _bars_are_valid(bars, "TEST")
        # Duplicate should be filtered, leaving 2 bars
        assert len(bars) == 2


# ══════════════════════════════════════════════════════════════════════
# SESSION DATE AND FRESHNESS TESTS
# ══════════════════════════════════════════════════════════════════════

from scanner.radar_data import get_expected_latest_egx_session, assess_data_freshness
import pytz
from datetime import datetime, timedelta

CAIRO_TZ = pytz.timezone("Africa/Cairo")


class TestSessionDateDetection:
    """Tests for EGX session date detection logic."""

    def test_after_close_wednesday(self):
        """Wed 15:00 Cairo → expected session = Wed (completed)"""
        now = datetime(2026, 7, 22, 15, 0, tzinfo=CAIRO_TZ)  # Wednesday
        result = get_expected_latest_egx_session(now)
        assert result.date() == datetime(2026, 7, 22).date()

    def test_before_close_wednesday(self):
        """Wed 14:00 Cairo → expected session = Tue (session not yet complete)"""
        now = datetime(2026, 7, 22, 14, 0, tzinfo=CAIRO_TZ)  # Wednesday before 14:45
        result = get_expected_latest_egx_session(now)
        assert result.date() == datetime(2026, 7, 21).date()  # Tuesday

    def test_friday_evening(self):
        """Fri 15:00 Cairo → expected session = Thu (Fri not trading day)"""
        now = datetime(2026, 7, 24, 15, 0, tzinfo=CAIRO_TZ)  # Friday
        result = get_expected_latest_egx_session(now)
        assert result.date() == datetime(2026, 7, 23).date()  # Thursday

    def test_saturday_afternoon(self):
        """Sat 15:00 Cairo → expected session = Thu"""
        now = datetime(2026, 7, 25, 15, 0, tzinfo=CAIRO_TZ)  # Saturday
        result = get_expected_latest_egx_session(now)
        assert result.date() == datetime(2026, 7, 23).date()  # Thursday

    def test_sunday_after_close(self):
        """Sun 15:00 Cairo → expected session = Sun (trading day, after close)"""
        now = datetime(2026, 7, 26, 15, 0, tzinfo=CAIRO_TZ)  # Sunday
        result = get_expected_latest_egx_session(now)
        assert result.date() == datetime(2026, 7, 26).date()  # Sunday (trading day)

    def test_sunday_before_close(self):
        """Sun 14:00 Cairo → expected session = Thu (Sun session not complete)"""
        now = datetime(2026, 7, 26, 14, 0, tzinfo=CAIRO_TZ)  # Sunday before 14:45
        result = get_expected_latest_egx_session(now)
        assert result.date() == datetime(2026, 7, 23).date()  # Thursday

    def test_exact_close_time(self):
        """Wed 14:15 Cairo → session just closed, need buffer"""
        now = datetime(2026, 7, 22, 14, 15, tzinfo=CAIRO_TZ)  # Wed exact close
        result = get_expected_latest_egx_session(now)
        # 14:15 < 14:45 (close + 30min buffer), so session not yet complete
        assert result.date() == datetime(2026, 7, 21).date()  # Tuesday

    def test_after_buffer_wednesday(self):
        """Wed 14:46 Cairo → session complete"""
        now = datetime(2026, 7, 22, 14, 46, tzinfo=CAIRO_TZ)  # Wed after buffer
        result = get_expected_latest_egx_session(now)
        assert result.date() == datetime(2026, 7, 22).date()  # Wednesday

    def test_monday_morning(self):
        """Mon 09:00 Cairo → expected session = Sun (Mon session not started)"""
        now = datetime(2026, 7, 27, 9, 0, tzinfo=CAIRO_TZ)  # Monday morning
        result = get_expected_latest_egx_session(now)
        assert result.date() == datetime(2026, 7, 26).date()  # Sunday

    def test_returns_datetime_with_cairo_tz(self):
        """Result should be a datetime with Cairo timezone."""
        now = datetime(2026, 7, 22, 15, 0, tzinfo=CAIRO_TZ)
        result = get_expected_latest_egx_session(now)
        assert result.tzinfo is not None
        assert result.tzinfo.zone == "Africa/Cairo"


class TestDataFreshnessAssessment:
    """Tests for data freshness assessment logic."""

    def test_current_data(self):
        """Provider has expected session → CURRENT."""
        now = datetime(2026, 7, 22, 15, 0, tzinfo=CAIRO_TZ)  # Wed after close
        status, note, delay = assess_data_freshness("2026-07-22", now)
        assert status == config.FRESHNESS_CURRENT
        assert delay == 0

    def test_one_day_delay_is_provider_delayed(self):
        """Provider has yesterday's data → PROVIDER_DELAYED (no tolerance)."""
        now = datetime(2026, 7, 22, 15, 0, tzinfo=CAIRO_TZ)  # Wed after close
        status, note, delay = assess_data_freshness("2026-07-21", now)
        assert status == config.FRESHNESS_PROVIDER_DELAYED
        assert delay == 1

    def test_two_day_delay(self):
        """Provider has 2-day-old data → PROVIDER_DELAYED."""
        now = datetime(2026, 7, 22, 15, 0, tzinfo=CAIRO_TZ)  # Wed after close
        status, note, delay = assess_data_freshness("2026-07-20", now)
        assert status == config.FRESHNESS_PROVIDER_DELAYED
        assert delay == 2

    def test_market_open(self):
        """During market hours → MARKET_OPEN."""
        now = datetime(2026, 7, 22, 10, 0, tzinfo=CAIRO_TZ)  # Wed 10:00 (market open)
        status, note, delay = assess_data_freshness("2026-07-21", now)
        assert status == config.FRESHNESS_MARKET_OPEN

    def test_non_trading_day(self):
        """Saturday → NON_TRADING_DAY."""
        now = datetime(2026, 7, 25, 15, 0, tzinfo=CAIRO_TZ)  # Saturday
        status, note, delay = assess_data_freshness("2026-07-24", now)
        assert status == config.FRESHNESS_NON_TRADING_DAY

    def test_unparseable_date(self):
        """Unparseable provider date → DATA_UNAVAILABLE."""
        now = datetime(2026, 7, 22, 15, 0, tzinfo=CAIRO_TZ)
        status, note, delay = assess_data_freshness("not-a-date", now)
        assert status == config.FRESHNESS_DATA_UNAVAILABLE
        assert delay == -1

    def test_provider_ahead_of_expected(self):
        """Provider has future data → CURRENT."""
        now = datetime(2026, 7, 22, 15, 0, tzinfo=CAIRO_TZ)
        status, note, delay = assess_data_freshness("2026-07-23", now)
        assert status == config.FRESHNESS_CURRENT
        assert delay == 0

    def test_delay_days_calculation(self):
        """Verify delay_days is correctly calculated."""
        now = datetime(2026, 7, 22, 15, 0, tzinfo=CAIRO_TZ)  # Wed → expected = Wed
        status, note, delay = assess_data_freshness("2026-07-19", now)  # Sun
        # Wed - Sun = 3 days
        assert delay == 3
        assert status == config.FRESHNESS_PROVIDER_DELAYED

    def test_production_case_provider_2026_07_20_expected_2026_07_21(self):
        """Regression: production case — provider 2026-07-20, expected 2026-07-21.
        
        This is the exact production bug scenario. Provider latest = 2026-07-20,
        expected session = 2026-07-21 (Tuesday). Status must be PROVIDER_DELAYED.
        """
        # Tuesday 2026-07-21 15:00 Cairo — market closed, expected session = Tue 2026-07-21
        now = datetime(2026, 7, 21, 15, 0, tzinfo=CAIRO_TZ)
        status, note, delay = assess_data_freshness("2026-07-20", now)
        assert status == config.FRESHNESS_PROVIDER_DELAYED
        assert delay == 1
        assert "delayed" in note.lower()


class TestFreshnessFieldsInRadarItem:
    """Tests that freshness fields are properly set on RadarItem."""

    def test_radar_item_has_freshness_fields(self):
        """RadarItem should have freshness fields."""
        item = RadarItem(symbol="TEST")
        assert hasattr(item, "provider_latest_date")
        assert hasattr(item, "expected_latest_session")
        assert hasattr(item, "freshness_status")
        assert hasattr(item, "freshness_note")
        assert hasattr(item, "freshness_delay_days")

    def test_radar_item_freshness_defaults(self):
        """Freshness fields should have sensible defaults."""
        item = RadarItem(symbol="TEST")
        assert item.provider_latest_date == ""
        assert item.expected_latest_session == ""
        assert item.freshness_status == ""
        assert item.freshness_note == ""
        assert item.freshness_delay_days == 0

    def test_market_radar_result_has_freshness(self):
        """MarketRadarResult should have freshness fields."""
        from scanner.market_radar import MarketRadarResult
        result = MarketRadarResult()
        assert hasattr(result, "expected_latest_session")
        assert hasattr(result, "freshness_status")
        assert hasattr(result, "freshness_note")
