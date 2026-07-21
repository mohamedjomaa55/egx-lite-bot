"""
Unit Tests for Shadow Provider
================================

Tests the comparison logic, status classification, CSV logging,
and summary generation — all without hitting any network.

Usage
-----
    python -m pytest tests/test_shadow.py -v
"""

import csv
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.shadow import (
    ShadowStatus,
    SymbolComparison,
    ShadowSummary,
    _classify_status,
    _append_csv,
    _csv_path,
    run_shadow_comparison,
)
from scanner import config


# ─── Fixtures ─────────────────────────────────────────────────────────
@pytest.fixture
def fresh_config():
    """Store and restore original config values."""
    orig_price = config.SHADOW_PRICE_MATCH_THRESHOLD
    orig_stale = config.SHADOW_STALE_THRESHOLD_SEC
    yield
    config.SHADOW_PRICE_MATCH_THRESHOLD = orig_price
    config.SHADOW_STALE_THRESHOLD_SEC = orig_stale


@pytest.fixture
def tmp_logs(tmp_path):
    """Redirect LOG_DIR to a temp directory."""
    import providers.shadow as shadow_mod
    original = shadow_mod.LOG_DIR
    shadow_mod.LOG_DIR = tmp_path / "logs"
    shadow_mod.LOG_DIR.mkdir(exist_ok=True)
    yield tmp_path / "logs"
    shadow_mod.LOG_DIR = original


# ─── ShadowStatus tests ──────────────────────────────────────────────
class TestShadowStatus:
    def test_all_statuses_defined(self):
        assert ShadowStatus.MATCH == "MATCH"
        assert ShadowStatus.PRICE_DIFF == "PRICE_DIFF"
        assert ShadowStatus.VOLUME_DIFF == "VOLUME_DIFF"
        assert ShadowStatus.STALE_DATA == "STALE_DATA"
        assert ShadowStatus.NO_DATA == "NO_DATA"


# ─── SymbolComparison tests ──────────────────────────────────────────
class TestSymbolComparison:
    def test_to_csv_row(self):
        rec = SymbolComparison(
            timestamp="2026-07-21T12:00:00Z",
            symbol="ARCC",
            provider_price=56.93,
            egxapi_price=56.95,
            difference_percent=0.035,
            provider_volume=100000,
            egxapi_volume=100000,
            provider_timestamp="2026-07-21T11:55:00Z",
            egxapi_timestamp="2026-07-21T11:58:00Z",
            status=ShadowStatus.MATCH,
        )
        row = rec.to_csv_row()
        assert row["symbol"] == "ARCC"
        assert row["provider_price"] == 56.93
        assert row["egxapi_price"] == 56.95
        assert row["status"] == "MATCH"
        assert "provider_high" not in row  # CSV row doesn't include non-CSV fields


# ─── ShadowSummary tests ─────────────────────────────────────────────
class TestShadowSummary:
    def test_to_text(self):
        s = ShadowSummary(
            timestamp="2026-07-21 12:00:00",
            symbols_compared=6,
            matches=4,
            price_differences=1,
            volume_differences=0,
            stale_data=1,
            no_data=0,
            match_rate_percent=66.7,
        )
        text = s.to_text()
        assert "6" in text
        assert "4" in text
        assert "66.7%" in text

    def test_to_dict(self):
        s = ShadowSummary(
            timestamp="2026-07-21 12:00:00",
            symbols_compared=6,
            matches=4,
            match_rate_percent=66.7,
        )
        d = s.to_dict()
        assert d["symbols_compared"] == 6
        assert d["matches"] == 4
        assert d["match_rate_percent"] == 66.7


# ─── _classify_status tests ───────────────────────────────────────────
class TestClassifyStatus:
    def test_both_none(self):
        rec = SymbolComparison(timestamp="t", symbol="X")
        assert _classify_status(rec) == ShadowStatus.NO_DATA

    def test_egxapi_none(self):
        rec = SymbolComparison(timestamp="t", symbol="X", provider_price=10.0)
        assert _classify_status(rec) == ShadowStatus.NO_DATA

    def test_provider_none(self):
        rec = SymbolComparison(timestamp="t", symbol="X", egxapi_price=10.0)
        assert _classify_status(rec) == ShadowStatus.NO_DATA

    def test_exact_match(self, fresh_config):
        config.SHADOW_PRICE_MATCH_THRESHOLD = 0.25
        now_iso = datetime.now(timezone.utc).isoformat()
        rec = SymbolComparison(
            timestamp="t", symbol="X",
            provider_price=100.0, egxapi_price=100.0,
            egxapi_timestamp=now_iso,
        )
        assert _classify_status(rec) == ShadowStatus.MATCH

    def test_small_difference_is_match(self, fresh_config):
        config.SHADOW_PRICE_MATCH_THRESHOLD = 0.25
        now_iso = datetime.now(timezone.utc).isoformat()
        rec = SymbolComparison(
            timestamp="t", symbol="X",
            provider_price=100.0, egxapi_price=100.2,
            egxapi_timestamp=now_iso,
        )
        status = _classify_status(rec)
        assert status == ShadowStatus.MATCH
        assert rec.difference_percent == pytest.approx(0.2, abs=0.01)

    def test_price_diff(self, fresh_config):
        config.SHADOW_PRICE_MATCH_THRESHOLD = 0.25
        now_iso = datetime.now(timezone.utc).isoformat()
        rec = SymbolComparison(
            timestamp="t", symbol="X",
            provider_price=100.0, egxapi_price=101.0,
            egxapi_timestamp=now_iso,
        )
        assert _classify_status(rec) == ShadowStatus.PRICE_DIFF

    def test_stale_data(self, fresh_config):
        config.SHADOW_STALE_THRESHOLD_SEC = 900
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        rec = SymbolComparison(
            timestamp="t", symbol="X",
            provider_price=100.0, egxapi_price=100.0,
            egxapi_timestamp=old_ts,
        )
        assert _classify_status(rec) == ShadowStatus.STALE_DATA

    def test_volume_diff(self, fresh_config):
        config.SHADOW_PRICE_MATCH_THRESHOLD = 0.25
        now_iso = datetime.now(timezone.utc).isoformat()
        rec = SymbolComparison(
            timestamp="t", symbol="X",
            provider_price=100.0, egxapi_price=100.0,
            provider_volume=1000, egxapi_volume=2000,
            egxapi_timestamp=now_iso,
        )
        assert _classify_status(rec) == ShadowStatus.VOLUME_DIFF

    def test_volume_match(self, fresh_config):
        config.SHADOW_PRICE_MATCH_THRESHOLD = 0.25
        now_iso = datetime.now(timezone.utc).isoformat()
        rec = SymbolComparison(
            timestamp="t", symbol="X",
            provider_price=100.0, egxapi_price=100.0,
            provider_volume=1000, egxapi_volume=1000,
            egxapi_timestamp=now_iso,
        )
        assert _classify_status(rec) == ShadowStatus.MATCH

    def test_volume_none_skips_check(self, fresh_config):
        config.SHADOW_PRICE_MATCH_THRESHOLD = 0.25
        now_iso = datetime.now(timezone.utc).isoformat()
        rec = SymbolComparison(
            timestamp="t", symbol="X",
            provider_price=100.0, egxapi_price=100.0,
            provider_volume=None, egxapi_volume=500,
            egxapi_timestamp=now_iso,
        )
        assert _classify_status(rec) == ShadowStatus.MATCH


# ─── CSV logging tests ───────────────────────────────────────────────
class TestCSVLogging:
    def test_csv_path(self, tmp_logs):
        path = _csv_path("20260721")
        assert path.name == "provider_validation_20260721.csv"
        assert path.parent == tmp_logs

    def test_append_csv_creates_file(self, tmp_logs):
        records = [
            SymbolComparison(
                timestamp="2026-07-21T12:00:00Z",
                symbol="ARCC",
                provider_price=56.93,
                egxapi_price=56.95,
                difference_percent=0.035,
                status=ShadowStatus.MATCH,
            )
        ]
        path = _append_csv(records)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "symbol" in content  # header
        assert "ARCC" in content

    def test_append_csv_adds_rows(self, tmp_logs):
        rec1 = SymbolComparison(
            timestamp="t1", symbol="A",
            provider_price=10, egxapi_price=10,
            status=ShadowStatus.MATCH,
        )
        rec2 = SymbolComparison(
            timestamp="t2", symbol="B",
            provider_price=20, egxapi_price=20,
            status=ShadowStatus.MATCH,
        )
        _append_csv([rec1])
        path = _append_csv([rec2])
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        # Header + 2 data rows
        assert len(lines) == 3

    def test_append_csv_correct_columns(self, tmp_logs):
        records = [
            SymbolComparison(
                timestamp="t", symbol="X",
                provider_price=10, egxapi_price=11,
                difference_percent=10.0,
                provider_volume=100, egxapi_volume=200,
                provider_timestamp="pt", egxapi_timestamp="et",
                status=ShadowStatus.PRICE_DIFF,
            )
        ]
        path = _append_csv(records)
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert row["symbol"] == "X"
        assert row["provider_price"] == "10"
        assert row["egxapi_price"] == "11"
        assert row["status"] == "PRICE_DIFF"


# ─── run_shadow_comparison tests ──────────────────────────────────────
class TestRunShadowComparison:
    def test_empty_tickers(self, tmp_logs):
        summary = run_shadow_comparison([])
        assert summary.symbols_compared == 0
        assert summary.match_rate_percent == 0.0

    @patch("providers.shadow.fetch_live_quote")
    @patch("providers.egxapi_provider.get_provider")
    def test_single_symbol_match(self, mock_get_provider, mock_fetch, tmp_logs):
        from providers.egxapi_provider import NormalizedQuote, QuoteState

        mock_fetch.return_value = {
            "last_traded_price": 100.0,
            "session_high": 102.0,
            "session_low": 98.0,
            "quote_time": datetime.now(timezone.utc).isoformat(),
            "source": "yfinance",
        }

        mock_egxapi = MagicMock()
        mock_egxapi.get_quote.return_value = NormalizedQuote(
            symbol="TEST",
            last_price=100.0,
            volume=5000,
            high=102.0,
            low=98.0,
            timestamp=datetime.now(timezone.utc).isoformat(),
            state=QuoteState.LIVE_VERIFIED.value,
        )
        mock_get_provider.return_value = mock_egxapi

        summary = run_shadow_comparison(["TEST"])
        assert summary.symbols_compared == 1
        assert summary.matches == 1
        assert summary.match_rate_percent == 100.0

    @patch("providers.shadow.fetch_live_quote")
    @patch("providers.egxapi_provider.get_provider")
    def test_single_symbol_price_diff(self, mock_get_provider, mock_fetch, tmp_logs):
        from providers.egxapi_provider import NormalizedQuote, QuoteState

        mock_fetch.return_value = {
            "last_traded_price": 100.0,
            "session_high": 102.0,
            "session_low": 98.0,
            "quote_time": datetime.now(timezone.utc).isoformat(),
            "source": "yfinance",
        }

        mock_egxapi = MagicMock()
        mock_egxapi.get_quote.return_value = NormalizedQuote(
            symbol="TEST",
            last_price=105.0,  # 5% difference
            volume=5000,
            timestamp=datetime.now(timezone.utc).isoformat(),
            state=QuoteState.LIVE_VERIFIED.value,
        )
        mock_get_provider.return_value = mock_egxapi

        summary = run_shadow_comparison(["TEST"])
        assert summary.symbols_compared == 1
        assert summary.price_differences == 1
        assert summary.matches == 0

    @patch("providers.shadow.fetch_live_quote")
    @patch("providers.egxapi_provider.get_provider")
    def test_egxapi_failure_is_non_blocking(self, mock_get_provider, mock_fetch, tmp_logs):
        mock_fetch.return_value = {
            "last_traded_price": 100.0,
            "quote_time": datetime.now(timezone.utc).isoformat(),
            "source": "yfinance",
        }
        mock_egxapi = MagicMock()
        mock_egxapi.get_quote.side_effect = Exception("network error")
        mock_get_provider.return_value = mock_egxapi

        summary = run_shadow_comparison(["TEST"])
        assert summary.symbols_compared == 1
        assert summary.no_data == 1

    @patch("providers.shadow.fetch_live_quote")
    @patch("providers.egxapi_provider.get_provider")
    def test_yahoo_failure_is_non_blocking(self, mock_get_provider, mock_fetch, tmp_logs):
        from providers.egxapi_provider import NormalizedQuote, QuoteState

        mock_fetch.side_effect = Exception("yfinance error")

        mock_egxapi = MagicMock()
        mock_egxapi.get_quote.return_value = NormalizedQuote(
            symbol="TEST",
            last_price=100.0,
            timestamp=datetime.now(timezone.utc).isoformat(),
            state=QuoteState.LIVE_VERIFIED.value,
        )
        mock_get_provider.return_value = mock_egxapi

        summary = run_shadow_comparison(["TEST"])
        assert summary.symbols_compared == 1
        assert summary.no_data == 1


# ─── Config tests ─────────────────────────────────────────────────────
class TestConfig:
    def test_data_provider_default(self):
        assert config.DATA_PROVIDER in ("fallback", "egxapi", "shadow")

    def test_shadow_thresholds(self):
        assert config.SHADOW_PRICE_MATCH_THRESHOLD > 0
        assert config.SHADOW_STALE_THRESHOLD_SEC > 0

    def test_data_provider_from_env(self):
        os.environ["DATA_PROVIDER"] = "fallback"
        import importlib
        import scanner.config
        importlib.reload(scanner.config)
        assert scanner.config.DATA_PROVIDER == "fallback"
        os.environ["DATA_PROVIDER"] = "shadow"
        importlib.reload(scanner.config)
        assert scanner.config.DATA_PROVIDER == "shadow"
