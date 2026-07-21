import yfinance as yf
import pandas as pd
import time
import logging
from datetime import datetime, timezone
from typing import Optional

from . import config

logging.getLogger("yfinance").disabled = True

_CACHE: dict[str, tuple] = {}
_CACHE_TTL = 300

logger = logging.getLogger(__name__)


def normalize_ticker(ticker: str) -> str:
    """
    Normalize EGX ticker for Yahoo Finance.
    - Already ends with .CA → keep as-is
    - Starts with ^ → keep as-is (index symbols)
    - Otherwise → append .CA
    """
    t = ticker.strip()
    if not t:
        return t
    if t.startswith("^"):
        return t
    if t.upper().endswith(".CA"):
        return t
    return f"{t}.CA"


def to_yahoo(ticker: str) -> Optional[str]:
    if not ticker:
        return None
    return normalize_ticker(ticker)


def fetch_history(ticker: str) -> pd.DataFrame:
    cache_key = f"{ticker}:{config.DATA_PERIOD}:{config.DATA_INTERVAL}"
    now = time.time()
    if cache_key in _CACHE:
        data, ts = _CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            return data

    yahoo_sym = to_yahoo(ticker)
    if not yahoo_sym:
        raise ValueError(f"Unknown ticker: {ticker}")

    def _fetch(period: str) -> pd.DataFrame:
        return yf.Ticker(yahoo_sym).history(period=period, interval=config.DATA_INTERVAL)

    data = _fetch(config.DATA_PERIOD)
    if data.empty:
        time.sleep(2)
        data = _fetch("1y")
    if data.empty:
        data = _fetch("1y")
    if data.empty:
        raise ValueError(f"No data for {ticker} (provider: {yahoo_sym})")

    _CACHE[cache_key] = (data, now)
    return data


def fetch_live_quote(ticker: str) -> dict:
    """
    Fetch live/real-time quote for a ticker.
    Returns dict with:
        last_traded_price, session_open, previous_close,
        session_high, session_low, quote_time, price_type, source

    Price type: LAST_TRADE / DAILY_CLOSE / PREVIOUS_CLOSE / FALLBACK
    """
    yahoo_sym = to_yahoo(ticker)
    if not yahoo_sym:
        return _empty_quote(ticker, "INVALID_TICKER")

    result = {
        "ticker": ticker,
        "last_traded_price": None,
        "session_open": None,
        "previous_close": None,
        "session_high": None,
        "session_low": None,
        "quote_time": None,
        "price_type": "FALLBACK",
        "source": "none",
    }

    # ── Source 1: fast_info ──────────────────────────────────────────
    try:
        fi = yf.Ticker(yahoo_sym).fast_info
        lp = fi.get("lastPrice") or fi.get("last_price")
        prev = fi.get("regularMarketPreviousClose") or fi.get("previousClose")
        opn = fi.get("open")
        day_hi = fi.get("dayHigh")
        day_lo = fi.get("dayLow")

        if lp and float(lp) > 0:
            result["last_traded_price"] = round(float(lp), 2)
            result["source"] = "fast_info.lastPrice"

        if prev and float(prev) > 0:
            result["previous_close"] = round(float(prev), 2)

        if opn and float(opn) > 0:
            result["session_open"] = round(float(opn), 2)

        if day_hi and float(day_hi) > 0:
            result["session_high"] = round(float(day_hi), 2)

        if day_lo and float(day_lo) > 0:
            result["session_low"] = round(float(day_lo), 2)

    except Exception as e:
        logger.debug(f"fast_info failed for {ticker}: {e}")

    # ── Sanity check: validate last_price against previous_close ────
    # Yahoo Finance often returns wrong lastPrice for EGX stocks
    # (e.g., 10.5 instead of 56.93). Reject if deviation > 30%.
    if result["last_traded_price"] is not None and result["previous_close"] is not None:
        price = result["last_traded_price"]
        prev_close = result["previous_close"]
        deviation = abs(price - prev_close) / prev_close
        if deviation > 0.30:
            logger.debug(
                f"Sanity check failed for {ticker}: "
                f"last_price={price}, prev_close={prev_close}, "
                f"deviation={deviation:.1%} > 30%. Rejecting."
            )
            result["last_traded_price"] = None
            result["source"] = "none"

    # ── Source 2: info dict ──────────────────────────────────────────
    if result["last_traded_price"] is None:
        try:
            info = yf.Ticker(yahoo_sym).info
            rmp = info.get("regularMarketPrice")
            if rmp and float(rmp) > 0:
                result["last_traded_price"] = round(float(rmp), 2)
                result["source"] = "info.regularMarketPrice"

            rmc = info.get("regularMarketPreviousClose")
            if rmc and float(rmc) > 0 and result["previous_close"] is None:
                result["previous_close"] = round(float(rmc), 2)

            rmo = info.get("regularMarketOpen")
            if rmo and float(rmo) > 0 and result["session_open"] is None:
                result["session_open"] = round(float(rmo), 2)

            rmh = info.get("regularMarketDayHigh")
            if rmh and float(rmh) > 0 and result["session_high"] is None:
                result["session_high"] = round(float(rmh), 2)

            rml = info.get("regularMarketDayLow")
            if rml and float(rml) > 0 and result["session_low"] is None:
                result["session_low"] = round(float(rml), 2)

        except Exception as e:
            logger.debug(f"info failed for {ticker}: {e}")

    # ── Sanity check: validate info price against previous_close ────
    if result["last_traded_price"] is not None and result["previous_close"] is not None:
        price = result["last_traded_price"]
        prev_close = result["previous_close"]
        deviation = abs(price - prev_close) / prev_close
        if deviation > 0.30:
            logger.debug(
                f"Sanity check failed for {ticker} (info): "
                f"price={price}, prev_close={prev_close}, "
                f"deviation={deviation:.1%} > 30%. Rejecting."
            )
            result["last_traded_price"] = None
            result["source"] = "none"

    # ── Source 3: Daily candle last Close ────────────────────────────
    if result["last_traded_price"] is None:
        try:
            hist = yf.Ticker(yahoo_sym).history(period="5d", interval="1d")
            if not hist.empty:
                last_close_val = float(hist["Close"].iloc[-1])
                last_open_val = float(hist["Open"].iloc[-1])
                last_high_val = float(hist["High"].iloc[-1])
                last_low_val = float(hist["Low"].iloc[-1])
                result["last_traded_price"] = round(last_close_val, 2)
                result["session_open"] = round(last_open_val, 2)
                result["session_high"] = round(last_high_val, 2)
                result["session_low"] = round(last_low_val, 2)
                result["source"] = "daily_candle.Close"

                if len(hist) >= 2:
                    prev_val = float(hist["Close"].iloc[-2])
                    result["previous_close"] = round(prev_val, 2)

        except Exception as e:
            logger.debug(f"daily candle failed for {ticker}: {e}")

    # ── Source 4: Previous close fallback ────────────────────────────
    if result["last_traded_price"] is None:
        if result["previous_close"] is not None:
            result["last_traded_price"] = result["previous_close"]
            result["price_type"] = "PREVIOUS_CLOSE"
            result["source"] = "fallback_previous_close"
        else:
            return _empty_quote(ticker, "NO_DATA")

    # ── Determine price_type ─────────────────────────────────────────
    if result["source"] in ("fast_info.lastPrice", "info.regularMarketPrice"):
        result["price_type"] = "LAST_TRADE"
    elif result["source"] == "daily_candle.Close":
        result["price_type"] = "DAILY_CLOSE"
    elif result["source"] in ("fallback_previous_close",):
        result["price_type"] = "PREVIOUS_CLOSE"
    else:
        result["price_type"] = "FALLBACK"

    result["quote_time"] = datetime.now(timezone.utc).isoformat()

    return result


def _empty_quote(ticker: str, reason: str) -> dict:
    return {
        "ticker": ticker,
        "last_traded_price": None,
        "session_open": None,
        "previous_close": None,
        "session_high": None,
        "session_low": None,
        "quote_time": None,
        "price_type": "FALLBACK",
        "source": reason,
    }


def fetch_index() -> pd.DataFrame:
    """Fetch the EGX30 index data for market filter."""
    return fetch_history(config.EGX_INDEX)


def get_all_tickers() -> list[str]:
    return sorted(config.EGX_SYMBOL_MAP.keys())
