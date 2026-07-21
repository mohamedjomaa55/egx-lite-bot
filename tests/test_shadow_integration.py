"""
Integration Tests for Shadow Provider
=======================================

Tests the full shadow comparison pipeline including thread pool,
CSV file creation, and summary generation with realistic data.

These tests use mocking to avoid network calls but test the full
integration path.

Usage
-----
    python -m pytest tests/test_shadow_integration.py -v
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
    run_shadow_comparison,
    ShadowStatus,
    _csv_path,
)
from providers.egxapi_provider import NormalizedQuote, QuoteState
from scanner import config


@pytest.fixture
def tmp_logs(tmp_path):
    """Redirect LOG_DIR to a temp directory."""
    import providers.shadow as shadow_mod
    original = shadow_mod.LOG_DIR
    shadow_mod.LOG_DIR = tmp_path / "logs"
    shadow_mod.LOG_DIR.mkdir(exist_ok=True)
    yield tmp_path / "logs"
    shadow_mod.LOG_DIR = original


def _make_yf_quote(price, high=102.0, low=98.0):
    return {
        "last_traded_price": price,
        "session_high": high,
        "session_low": low,
        "quote_time": datetime.now(timezone.utc).isoformat(),
        "source": "yfinance",
    }


def _make_egxapi_quote(symbol, price, volume=5000, ts=None):
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    return NormalizedQuote(
        symbol=symbol,
        last_price=price,
        bid=price - 0.1,
        ask=price + 0.1,
        high=price + 2,
        low=price - 2,
        volume=volume,
        timestamp=ts,
        state=QuoteState.LIVE_VERIFIED.value,
    )


# ─── Full pipeline integration ───────────────────────────────────────
class TestShadowPipeline:
    @patch("providers.shadow.fetch_live_quote")
    @patch("providers.egxapi_provider.get_provider")
    def test_full_pipeline_all_match(self, mock_get_provider, mock_fetch, tmp_logs):
        """All 6 symbols match - 100% match rate."""
        symbols = ["ARCC", "COMI", "ETEL", "EGAL", "TMGH", "FWRY"]
        prices = [56.93, 45.20, 21.50, 12.80, 33.10, 8.90]

        def fake_yf(ticker):
            idx = symbols.index(ticker) if ticker in symbols else 0
            return _make_yf_quote(prices[idx])

        mock_fetch.side_effect = fake_yf

        mock_egxapi = MagicMock()
        def fake_egxapi(symbol):
            idx = symbols.index(symbol) if symbol in symbols else 0
            return _make_egxapi_quote(symbol, prices[idx])

        mock_egxapi.get_quote.side_effect = fake_egxapi
        mock_get_provider.return_value = mock_egxapi

        summary = run_shadow_comparison(symbols)

        assert summary.symbols_compared == 6
        assert summary.matches == 6
        assert summary.price_differences == 0
        assert summary.no_data == 0
        assert summary.match_rate_percent == 100.0

        # Check CSV was created
        csv_files = list(tmp_logs.glob("provider_validation_*.csv"))
        assert len(csv_files) == 1

        with open(csv_files[0], "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 6
        for row in rows:
            assert row["status"] == "MATCH"

    @patch("providers.shadow.fetch_live_quote")
    @patch("providers.egxapi_provider.get_provider")
    def test_full_pipeline_mixed_results(self, mock_get_provider, mock_fetch, tmp_logs):
        """Mixed results: some match, some differ, some no data."""
        symbols = ["ARCC", "COMI", "ETEL"]

        mock_fetch.side_effect = lambda t: _make_yf_quote(100.0)

        mock_egxapi = MagicMock()
        def fake_egxapi(symbol):
            if symbol == "ARCC":
                return _make_egxapi_quote(symbol, 100.0)      # match
            elif symbol == "COMI":
                return _make_egxapi_quote(symbol, 105.0)      # price diff
            else:
                raise Exception("API down")                    # no data

        mock_egxapi.get_quote.side_effect = fake_egxapi
        mock_get_provider.return_value = mock_egxapi

        summary = run_shadow_comparison(symbols)

        assert summary.symbols_compared == 3
        assert summary.matches == 1
        assert summary.price_differences == 1
        assert summary.no_data == 1
        assert 0 < summary.match_rate_percent < 100

    @patch("providers.shadow.fetch_live_quote")
    @patch("providers.egxapi_provider.get_provider")
    def test_full_pipeline_egxapi_completely_down(self, mock_get_provider, mock_fetch, tmp_logs):
        """EGXAPI is completely down — all symbols should be NO_DATA."""
        symbols = ["ARCC", "COMI"]

        mock_fetch.side_effect = lambda t: _make_yf_quote(100.0)

        mock_egxapi = MagicMock()
        mock_egxapi.get_quote.side_effect = ConnectionError("refused")
        mock_get_provider.return_value = mock_egxapi

        summary = run_shadow_comparison(symbols)

        assert summary.symbols_compared == 2
        assert summary.no_data == 2
        assert summary.matches == 0
        assert summary.match_rate_percent == 0.0

    @patch("providers.shadow.fetch_live_quote")
    @patch("providers.egxapi_provider.get_provider")
    def test_full_pipeline_yahoo_completely_down(self, mock_get_provider, mock_fetch, tmp_logs):
        """Yahoo is completely down — all symbols should be NO_DATA."""
        symbols = ["ARCC", "COMI"]

        mock_fetch.side_effect = ConnectionError("Yahoo down")

        mock_egxapi = MagicMock()
        mock_egxapi.get_quote.side_effect = lambda s: _make_egxapi_quote(s, 100.0)
        mock_get_provider.return_value = mock_egxapi

        summary = run_shadow_comparison(symbols)

        assert summary.symbols_compared == 2
        assert summary.no_data == 2

    @patch("providers.shadow.fetch_live_quote")
    @patch("providers.egxapi_provider.get_provider")
    def test_csv_has_all_columns(self, mock_get_provider, mock_fetch, tmp_logs):
        """CSV file has exactly the expected columns."""
        mock_fetch.side_effect = lambda t: _make_yf_quote(100.0)
        mock_egxapi = MagicMock()
        mock_egxapi.get_quote.side_effect = lambda s: _make_egxapi_quote(s, 100.0)
        mock_get_provider.return_value = mock_egxapi

        run_shadow_comparison(["ARCC"])

        csv_files = list(tmp_logs.glob("provider_validation_*.csv"))
        assert len(csv_files) == 1

        with open(csv_files[0], "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            expected_cols = {
                "timestamp", "symbol", "provider_price", "egxapi_price",
                "difference_percent", "provider_volume", "egxapi_volume",
                "provider_timestamp", "egxapi_timestamp", "status",
            }
            assert set(reader.fieldnames) == expected_cols

    @patch("providers.shadow.fetch_live_quote")
    @patch("providers.egxapi_provider.get_provider")
    def test_multiple_appends_to_same_csv(self, mock_get_provider, mock_fetch, tmp_logs):
        """Multiple runs append to the same daily CSV file."""
        mock_fetch.side_effect = lambda t: _make_yf_quote(100.0)
        mock_egxapi = MagicMock()
        mock_egxapi.get_quote.side_effect = lambda s: _make_egxapi_quote(s, 100.0)
        mock_get_provider.return_value = mock_egxapi

        run_shadow_comparison(["ARCC"])
        run_shadow_comparison(["COMI"])

        csv_files = list(tmp_logs.glob("provider_validation_*.csv"))
        assert len(csv_files) == 1  # Same date, same file

        with open(csv_files[0], "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2  # Two symbols total

    @patch("providers.shadow.fetch_live_quote")
    @patch("providers.egxapi_provider.get_provider")
    def test_summary_text_output(self, mock_get_provider, mock_fetch, tmp_logs):
        """Summary text is human-readable and contains key metrics."""
        mock_fetch.side_effect = lambda t: _make_yf_quote(100.0)
        mock_egxapi = MagicMock()
        mock_egxapi.get_quote.side_effect = lambda s: _make_egxapi_quote(s, 100.0)
        mock_get_provider.return_value = mock_egxapi

        summary = run_shadow_comparison(["ARCC", "COMI"])
        text = summary.to_text()

        assert "Shadow Validation Summary" in text
        assert "2" in text  # symbols_compared
        assert "100.0%" in text  # match_rate


# ─── Edge cases ───────────────────────────────────────────────────────
class TestEdgeCases:
    def test_empty_tickers_list(self, tmp_logs):
        summary = run_shadow_comparison([])
        assert summary.symbols_compared == 0
        assert summary.match_rate_percent == 0.0

    @patch("providers.shadow.fetch_live_quote")
    @patch("providers.egxapi_provider.get_provider")
    def test_provider_price_zero(self, mock_get_provider, mock_fetch, tmp_logs):
        """Provider returns price=0 — should be NO_DATA or PRICE_DIFF."""
        mock_fetch.return_value = _make_yf_quote(0.0)
        mock_egxapi = MagicMock()
        mock_egxapi.get_quote.return_value = _make_egxapi_quote("TEST", 100.0)
        mock_get_provider.return_value = mock_egxapi

        summary = run_shadow_comparison(["TEST"])
        assert summary.symbols_compared == 1
        # With provider_price=0, the classify logic handles division safely

    @patch("providers.shadow.fetch_live_quote")
    @patch("providers.egxapi_provider.get_provider")
    def test_egxapi_returns_none_price(self, mock_get_provider, mock_fetch, tmp_logs):
        """EGXAPI returns last_price=None — should be NO_DATA."""
        mock_fetch.return_value = _make_yf_quote(100.0)
        mock_egxapi = MagicMock()
        mock_egxapi.get_quote.return_value = NormalizedQuote(
            symbol="TEST", last_price=None,
            state=QuoteState.DATA_UNAVAILABLE.value,
        )
        mock_get_provider.return_value = mock_egxapi

        summary = run_shadow_comparison(["TEST"])
        assert summary.symbols_compared == 1
        assert summary.no_data == 1

    @patch("providers.shadow.fetch_live_quote")
    @patch("providers.egxapi_provider.get_provider")
    def test_stale_egxapi_timestamp(self, mock_get_provider, mock_fetch, tmp_logs):
        """EGXAPI timestamp older than 15 min — should be STALE_DATA."""
        config.SHADOW_STALE_THRESHOLD_SEC = 900
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()

        mock_fetch.return_value = _make_yf_quote(100.0)
        mock_egxapi = MagicMock()
        mock_egxapi.get_quote.return_value = _make_egxapi_quote("TEST", 100.0, ts=old_ts)
        mock_get_provider.return_value = mock_egxapi

        summary = run_shadow_comparison(["TEST"])
        assert summary.symbols_compared == 1
        assert summary.stale_data == 1

    @patch("providers.shadow.fetch_live_quote")
    @patch("providers.egxapi_provider.get_provider")
    def test_scan_results_volume_used(self, mock_get_provider, mock_fetch, tmp_logs):
        """Volume from scan_results is used for provider_volume."""
        mock_fetch.return_value = _make_yf_quote(100.0)
        mock_egxapi = MagicMock()
        mock_egxapi.get_quote.return_value = _make_egxapi_quote("TEST", 100.0, volume=5000)
        mock_get_provider.return_value = mock_egxapi

        scan_results = [{"ticker": "TEST", "last_volume": 5000}]
        summary = run_shadow_comparison(["TEST"], scan_results=scan_results)

        # Volume matches, should be MATCH (not VOLUME_DIFF)
        assert summary.matches == 1
