"""
Tests for Card-Style Telegram Formatter
========================================

Covers progress bars, stock cards, headers, message splitting,
and all radar command formatting.

Usage
-----
    python -m pytest tests/test_telegram_formatter.py -v
"""

import os
import sys
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import config
from scanner.market_radar import (
    MarketRadarResult,
    RadarItem,
    RadarStats,
    ActivityCategory,
    ActivityLevel,
)
from scanner.radar_output import (
    build_activity_bar,
    format_market_status,
    format_short_reason,
    format_stock_card,
    format_radar_header,
    format_radar_footer,
    format_radar_category_section,
    split_radar_messages,
    format_radar_telegram_v2,
    _date_dd_mon_yyyy,
    _has_delay,
)


def _make_item(**kwargs):
    defaults = {
        "symbol": "TEST",
        "company_name": "Test Co",
        "price": 100.0,
        "latest_close": 100.0,
        "previous_close": 98.0,
        "session_open": 99.0,
        "session_high": 101.0,
        "session_low": 98.5,
        "display_price": 100.0,
        "price_date": "2026-07-22",
        "price_change_percent": 2.0,
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
        "macd_line": 0.5,
        "macd_signal_line": 0.2,
        "close_location_value": 0.8,
        "candle_body_percent": 70.0,
        "volume_percentile_60": 95.0,
        "activity_score": 67,
        "activity_score_components": {},
        "activity_category": ActivityCategory.BUYING,
        "activity_level": ActivityLevel.HIGH,
        "activity_label": "Moderate buying activity",
        "reasons": ["RVOL 3.0x versus 20-day average", "RSI rose from 55 to 60"],
        "price_return_5d": 3.0,
        "freshness_status": config.FRESHNESS_CURRENT,
        "freshness_note": "data is current",
    }
    defaults.update(kwargs)
    return RadarItem(**defaults)


def _make_result(items=None, freshness_status=None):
    if items is None:
        items = []
    if freshness_status is None:
        freshness_status = config.FRESHNESS_CURRENT
    stats = RadarStats(
        symbols_scanned=34,
        activity_detected=len(items),
        buying_count=sum(1 for i in items if i.activity_category == ActivityCategory.BUYING),
        selling_count=sum(1 for i in items if i.activity_category == ActivityCategory.SELLING),
        unusual_count=sum(1 for i in items if i.activity_category == ActivityCategory.UNUSUAL),
    )
    return MarketRadarResult(
        data_date="2026-07-22",
        expected_latest_session="2026-07-22",
        freshness_status=freshness_status,
        freshness_note="data is current",
        stats=stats,
        items=items,
        all_items=items,
    )


# ══════════════════════════════════════════════════════════════════════
# PROGRESS BAR TESTS
# ══════════════════════════════════════════════════════════════════════
class TestActivityBar:
    def test_bar_zero_score(self):
        bar = build_activity_bar(0, ActivityCategory.BUYING)
        assert "\u2b1c" * 10 in bar
        assert "\U0001f7e9" not in bar
        assert "0%" in bar

    def test_bar_33_score_buying(self):
        bar = build_activity_bar(33, ActivityCategory.BUYING)
        assert "\U0001f7e9" in bar
        assert "33%" in bar
        filled = bar.count("\U0001f7e9")
        assert filled == 3

    def test_bar_40_score_selling(self):
        bar = build_activity_bar(40, ActivityCategory.SELLING)
        assert "\U0001f534" in bar
        assert "40%" in bar
        filled = bar.count("\U0001f534")
        assert filled == 4

    def test_bar_67_score_buying(self):
        bar = build_activity_bar(67, ActivityCategory.BUYING)
        filled = bar.count("\U0001f7e9")
        assert filled == 7
        assert "67%" in bar

    def test_bar_67_score_selling(self):
        bar = build_activity_bar(67, ActivityCategory.SELLING)
        filled = bar.count("\U0001f534")
        assert filled == 7
        assert "67%" in bar

    def test_bar_73_score_unusual(self):
        bar = build_activity_bar(73, ActivityCategory.UNUSUAL)
        filled = bar.count("\U0001f7e8")
        assert filled == 7
        assert "73%" in bar

    def test_bar_94_score_buying(self):
        bar = build_activity_bar(94, ActivityCategory.BUYING)
        filled = bar.count("\U0001f7e9")
        assert filled == 9
        assert "94%" in bar

    def test_bar_100_score(self):
        bar = build_activity_bar(100, ActivityCategory.BUYING)
        filled = bar.count("\U0001f7e9")
        assert filled == 10
        assert "100%" in bar
        assert "\u2b1c" not in bar

    def test_bar_total_blocks_always_10(self):
        for score in [0, 15, 33, 40, 50, 67, 73, 85, 94, 100]:
            for cat in [ActivityCategory.BUYING, ActivityCategory.SELLING, ActivityCategory.UNUSUAL]:
                bar = build_activity_bar(score, cat)
                parts = bar.split()[0]
                assert len(parts) == 10, f"Score {score} cat {cat}: {len(parts)} blocks"


# ══════════════════════════════════════════════════════════════════════
# MARKET STATUS TESTS
# ══════════════════════════════════════════════════════════════════════
class TestMarketStatus:
    def test_market_open(self):
        result = format_market_status(config.FRESHNESS_MARKET_OPEN)
        assert "OPEN" in result
        assert "\U0001f7e2" in result

    def test_current_closed(self):
        result = format_market_status(config.FRESHNESS_CURRENT)
        assert "CLOSED" in result
        assert "\u2705" in result

    def test_provider_delayed(self):
        result = format_market_status(config.FRESHNESS_PROVIDER_DELAYED)
        assert "DELAYED" in result
        assert "\u26a0" in result

    def test_non_trading_day(self):
        result = format_market_status(config.FRESHNESS_NON_TRADING_DAY)
        assert "CLOSED" in result
        assert "\u26aa" in result

    def test_data_unavailable(self):
        result = format_market_status(config.FRESHNESS_DATA_UNAVAILABLE)
        assert "DATA UNAVAILABLE" in result
        assert "\U0001f534" in result

    def test_provider_delayed_has_delay_flag(self):
        assert _has_delay(config.FRESHNESS_PROVIDER_DELAYED) is True

    def test_current_no_delay_flag(self):
        assert _has_delay(config.FRESHNESS_CURRENT) is False

    def test_market_open_no_delay_flag(self):
        assert _has_delay(config.FRESHNESS_MARKET_OPEN) is False


# ══════════════════════════════════════════════════════════════════════
# REASON SHORTENING TESTS
# ══════════════════════════════════════════════════════════════════════
class TestShortReason:
    def test_rvol_high(self):
        result = format_short_reason("RVOL 2.1x versus 20-day average", ActivityCategory.BUYING)
        assert result == "High relative volume"

    def test_volume_average(self):
        result = format_short_reason("Volume 1.5x average", ActivityCategory.BUYING)
        assert result == "High relative volume"

    def test_traded_value_above(self):
        result = format_short_reason("Traded value 2.0x above normal", ActivityCategory.BUYING)
        assert result == "Strong traded value"

    def test_traded_value_below(self):
        result = format_short_reason("Traded value below average", ActivityCategory.SELLING)
        assert result == "Weak traded value"

    def test_rsi_rose(self):
        result = format_short_reason("RSI rose from 53 to 61", ActivityCategory.BUYING)
        assert result == "RSI rising"

    def test_rsi_fell(self):
        result = format_short_reason("RSI fell from 78 to 69", ActivityCategory.SELLING)
        assert result == "RSI falling"

    def test_close_near_high(self):
        result = format_short_reason("Close finished near the session high", ActivityCategory.BUYING)
        assert result == "Closed near session high"

    def test_close_near_low(self):
        result = format_short_reason("Close finished near the session low", ActivityCategory.SELLING)
        assert result == "Closed near session low"

    def test_macd_improving(self):
        result = format_short_reason("MACD histogram improving", ActivityCategory.BUYING)
        assert result == "MACD improving"

    def test_macd_weakening(self):
        result = format_short_reason("MACD histogram weakening", ActivityCategory.SELLING)
        assert result == "MACD weakening"

    def test_high_volume_limited_price(self):
        result = format_short_reason("High volume with limited price movement", ActivityCategory.UNUSUAL)
        assert result == "Volume without movement"


# ══════════════════════════════════════════════════════════════════════
# STOCK CARD TESTS
# ══════════════════════════════════════════════════════════════════════
class TestStockCard:
    def test_buying_card_structure(self):
        item = _make_item(
            symbol="ETEL",
            latest_close=106.00,
            price_change_percent=2.3,
            session_open=103.61,
            rvol_20=1.6,
            rsi_14=75,
            rsi_change=5.0,
            macd_histogram_change=0.3,
            activity_score=67,
            activity_label="Moderate buying activity",
            activity_category=ActivityCategory.BUYING,
        )
        card = format_stock_card(item, ActivityCategory.BUYING, 1)
        assert "ETEL" in card
        assert "106.00" in card
        assert "+2.3%" in card
        assert "103.61" in card
        assert "1.6x" in card
        assert "75" in card
        assert "Improving" in card
        assert "67%" in card
        assert "\U0001f3af" in card

    def test_selling_card_structure(self):
        item = _make_item(
            symbol="LCSW",
            latest_close=33.83,
            price_change_percent=-3.3,
            session_open=35.00,
            rvol_20=0.9,
            rsi_14=69,
            rsi_change=-3.0,
            macd_histogram_change=-0.3,
            activity_score=40,
            activity_label="Moderate selling activity",
            activity_category=ActivityCategory.SELLING,
        )
        card = format_stock_card(item, ActivityCategory.SELLING, 1)
        assert "LCSW" in card
        assert "33.83" in card
        assert "-3.3%" in card
        assert "35.00" in card
        assert "0.9x" in card
        assert "69" in card
        assert "Weakening" in card
        assert "40%" in card
        assert "\u26a0" in card

    def test_watch_card_structure(self):
        item = _make_item(
            symbol="ACGC",
            latest_close=10.20,
            price_change_percent=1.0,
            session_open=10.10,
            rvol_20=3.1,
            activity_score=73,
            activity_label="Unusual activity \u2014 direction unclear",
            activity_category=ActivityCategory.UNUSUAL,
        )
        card = format_stock_card(item, ActivityCategory.UNUSUAL, 1)
        assert "ACGC" in card
        assert "10.20" in card
        assert "+1.0%" in card
        assert "10.10" in card
        assert "3.1x" in card
        assert "73%" in card
        assert "\U0001f440" in card

    def test_missing_rsi_hidden(self):
        item = _make_item(rsi_14=50.0, rsi_change=0.0, rsi_previous=50.0, reasons=[])
        card = format_stock_card(item, ActivityCategory.BUYING, 1)
        assert "RSI" not in card

    def test_missing_macd_hidden(self):
        item = _make_item(macd_histogram_change=0.0, reasons=[])
        card = format_stock_card(item, ActivityCategory.BUYING, 1)
        assert "MACD" not in card

    def test_missing_rvol_hidden(self):
        item = _make_item(rvol_20=0.0, reasons=[])
        card = format_stock_card(item, ActivityCategory.BUYING, 1)
        assert "RVOL" not in card

    def test_positive_change(self):
        item = _make_item(price_change_percent=5.0)
        card = format_stock_card(item, ActivityCategory.BUYING, 1)
        assert "+5.0%" in card

    def test_negative_change(self):
        item = _make_item(price_change_percent=-2.5)
        card = format_stock_card(item, ActivityCategory.SELLING, 1)
        assert "-2.5%" in card

    def test_zero_change(self):
        item = _make_item(price_change_percent=0.0)
        card = format_stock_card(item, ActivityCategory.UNUSUAL, 1)
        assert "0.0%" in card

    def test_card_never_empty_lines(self):
        item = _make_item()
        card = format_stock_card(item, ActivityCategory.BUYING, 1)
        lines = card.split("\n")
        consecutive_empty = 0
        for line in lines:
            if line.strip() == "":
                consecutive_empty += 1
                assert consecutive_empty <= 2, "Too many consecutive empty lines"
            else:
                consecutive_empty = 0


# ══════════════════════════════════════════════════════════════════════
# HEADER TESTS
# ══════════════════════════════════════════════════════════════════════
class TestHeader:
    def test_market_open_header(self):
        items = [_make_item(activity_category=ActivityCategory.BUYING)]
        result = _make_result(items=items, freshness_status=config.FRESHNESS_MARKET_OPEN)
        header = format_radar_header(result, 20)
        assert "OPEN" in header
        assert "22 Jul 2026" in header
        assert "BUY:" in header
        assert "SELL:" in header
        assert "WATCH:" in header

    def test_current_closed_header(self):
        result = _make_result(freshness_status=config.FRESHNESS_CURRENT)
        header = format_radar_header(result, 20)
        assert "CLOSED" in header

    def test_provider_delayed_header(self):
        result = _make_result(freshness_status=config.FRESHNESS_PROVIDER_DELAYED)
        result.freshness_note = "data is 1 day old"
        header = format_radar_header(result, 20)
        assert "DELAYED" in header
        assert "Provider delay" in header

    def test_header_shows_scanned_count(self):
        result = _make_result()
        result.stats.symbols_scanned = 34
        header = format_radar_header(result, 20)
        assert "Scanned: 34" in header

    def test_header_shows_signal_count(self):
        items = [
            _make_item(symbol="A", activity_category=ActivityCategory.BUYING),
            _make_item(symbol="B", activity_category=ActivityCategory.SELLING),
        ]
        result = _make_result(items=items)
        header = format_radar_header(result, 20)
        assert "Signals: 2" in header

    def test_date_format(self):
        assert _date_dd_mon_yyyy("2026-07-22") == "22 Jul 2026"
        assert _date_dd_mon_yyyy("2026-01-05") == "05 Jan 2026"
        assert _date_dd_mon_yyyy("invalid") == "invalid"


# ══════════════════════════════════════════════════════════════════════
# FOOTER TESTS
# ══════════════════════════════════════════════════════════════════════
class TestFooter:
    def test_footer_content(self):
        footer = format_radar_footer()
        assert "Lite detects market activity" in footer
        assert "ISM" in footer
        assert "Entry" in footer
        assert "Stop Loss" in footer
        assert "Targets" in footer
        assert "\u2501" in footer


# ══════════════════════════════════════════════════════════════════════
# CATEGORY SECTION TESTS
# ══════════════════════════════════════════════════════════════════════
class TestCategorySection:
    def test_buying_section_header(self):
        item = _make_item(symbol="A", activity_category=ActivityCategory.BUYING)
        section = format_radar_category_section(ActivityCategory.BUYING, [item])
        assert "BUYING SIGNALS" in section
        assert "A" in section

    def test_selling_section_header(self):
        item = _make_item(symbol="B", activity_category=ActivityCategory.SELLING)
        section = format_radar_category_section(ActivityCategory.SELLING, [item])
        assert "SELLING SIGNALS" in section
        assert "B" in section

    def test_unusual_section_header(self):
        item = _make_item(symbol="C", activity_category=ActivityCategory.UNUSUAL)
        section = format_radar_category_section(ActivityCategory.UNUSUAL, [item])
        assert "WATCHLIST ACTIVITY" in section
        assert "C" in section

    def test_section_rank_numbering(self):
        items = [_make_item(symbol="X"), _make_item(symbol="Y")]
        section = format_radar_category_section(ActivityCategory.BUYING, items, start_rank=3)
        assert "3. X" in section
        assert "4. Y" in section


# ══════════════════════════════════════════════════════════════════════
# MESSAGE SPLITTING TESTS
# ══════════════════════════════════════════════════════════════════════
class TestMessageSplitting:
    def test_short_message_single_part(self):
        parts = ["Header\nContent\nFooter"]
        messages = split_radar_messages(parts)
        assert len(messages) == 1
        assert "Header" in messages[0]

    def test_long_message_splits(self):
        parts = ["A" * 3000, "B" * 3000, "C" * 3000]
        messages = split_radar_messages(parts, max_length=4096)
        assert len(messages) >= 2
        for msg in messages:
            assert len(msg) <= 4096

    def test_stock_card_never_split(self):
        card = "━" * 24 + "\nETEL\n\n" + "\U0001f7e9" * 7 + "\u2b1c" * 3 + " 67%\n\n\u2705 106.00"
        parts = ["Header\n" + card + "\nFooter"]
        messages = split_radar_messages(parts, max_length=4096)
        assert len(messages) == 1
        assert "ETEL" in messages[0]

    def test_empty_parts_returns_warning(self):
        messages = split_radar_messages([])
        assert len(messages) == 1
        assert "\u26a0" in messages[0]

    def test_large_card_stays_intact(self):
        reasons = ["Reason " * 10] * 4
        card_lines = ["━" * 24, "ETEL", "", "\U0001f7e9" * 7 + "\u2b1c" * 3 + " 67%", ""]
        card_lines.extend(reasons)
        card = "\n".join(card_lines)
        parts = [card]
        messages = split_radar_messages(parts, max_length=4096)
        found = any("ETEL" in m for m in messages)
        assert found


# ══════════════════════════════════════════════════════════════════════
# FULL RADAR V2 FORMAT TESTS
# ══════════════════════════════════════════════════════════════════════
class TestRadarV2:
    def test_v2_returns_list(self):
        items = [_make_item(symbol="A", activity_category=ActivityCategory.BUYING)]
        result = _make_result(items=items)
        messages = format_radar_telegram_v2(result)
        assert isinstance(messages, list)
        assert len(messages) >= 1

    def test_v2_all_categories_present(self):
        items = [
            _make_item(symbol="B", activity_category=ActivityCategory.BUYING),
            _make_item(symbol="C", activity_category=ActivityCategory.SELLING),
            _make_item(symbol="D", activity_category=ActivityCategory.UNUSUAL),
        ]
        result = _make_result(items=items)
        messages = format_radar_telegram_v2(result)
        full_text = "\n".join(messages)
        assert "BUYING SIGNALS" in full_text
        assert "SELLING SIGNALS" in full_text
        assert "WATCHLIST ACTIVITY" in full_text

    def test_v2_empty_items(self):
        result = _make_result(items=[])
        messages = format_radar_telegram_v2(result)
        full_text = "\n".join(messages)
        assert "No significant activity" in full_text or "\u26a0" in full_text

    def test_v2_all_commands_use_same_formatter(self):
        """All radar commands produce output through the same v2 formatter."""
        items = [_make_item(symbol="X", activity_category=ActivityCategory.BUYING)]
        result = _make_result(items=items)
        messages = format_radar_telegram_v2(result, top_n=10)
        assert len(messages) >= 1
        full = "\n".join(messages)
        assert "EGX LITE MARKET RADAR" in full
        assert "X" in full

    def test_v2_provider_delayed_includes_warning(self):
        result = _make_result(freshness_status=config.FRESHNESS_PROVIDER_DELAYED)
        result.freshness_note = "data is 1 day old"
        messages = format_radar_telegram_v2(result)
        full = "\n".join(messages)
        assert "DELAYED" in full
        assert "Provider delay" in full

    def test_v2_each_message_within_limit(self):
        items = [_make_item(symbol=f"S{i}", activity_category=ActivityCategory.BUYING) for i in range(15)]
        result = _make_result(items=items)
        messages = format_radar_telegram_v2(result)
        for msg in messages:
            assert len(msg) <= 4096

    def test_v2_stock_cards_are_separate_parts(self):
        items = [_make_item(symbol="A"), _make_item(symbol="B")]
        result = _make_result(items=items)
        messages = format_radar_telegram_v2(result)
        full = "\n".join(messages)
        assert "A" in full
        assert "B" in full

    def test_v2_footer_present(self):
        items = [_make_item()]
        result = _make_result(items=items)
        messages = format_radar_telegram_v2(result)
        full = "\n".join(messages)
        assert "Lite detects market activity" in full
        assert "ISM" in full
