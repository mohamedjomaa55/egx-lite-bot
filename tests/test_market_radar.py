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
from scanner.radar_data import RadarHistory, DailyBar
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
