import yfinance as yf
import pandas as pd
import numpy as np
import httpx
import pytz
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from . import config

logging.getLogger("yfinance").disabled = True

_CACHE: dict[str, tuple] = {}
_CACHE_TTL = 300

_TV_CACHE: dict[str, dict] = {}
_TV_CACHE_TTL = 60
_TV_SCAN_URL = "https://scanner.tradingview.com/egypt/scan"
_TV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
_CAIRO_TZ = pytz.timezone("Africa/Cairo")

logger = logging.getLogger(__name__)


def clear_cache():
    """Clear all data caches (Yahoo + TradingView)."""
    _CACHE.clear()
    _TV_CACHE.clear()
    logger.info("All data caches cleared")


def _is_market_hours() -> bool:
    """Check if EGX market is currently in a trading session (Cairo time)."""
    now = datetime.now(_CAIRO_TZ)
    if now.weekday() not in config.EGX_TRADING_DAYS:
        return False
    session_start = now.replace(hour=config.EGX_OPEN_HOUR, minute=config.EGX_OPEN_MINUTE, second=0, microsecond=0)
    session_end = now.replace(hour=config.EGX_CLOSE_HOUR, minute=config.EGX_CLOSE_MINUTE, second=0, microsecond=0)
    return session_start <= now <= session_end


def _tv_batch_fetch() -> dict[str, dict]:
    """Fetch all EGX stocks from TradingView scanner in a single batch request.

    Returns dict keyed by ticker symbol (e.g. 'EFIH') with values:
    {close, open, high, low, volume, change_pct, previous_close}
    """
    now = time.time()
    if _TV_CACHE and (now - list(_TV_CACHE.values())[0].get("_ts", 0)) < _TV_CACHE_TTL:
        return _TV_CACHE

    tickers = list(config.EGX_SYMBOL_MAP.keys())
    tv_symbols = [f"EGX:{t}" for t in tickers]

    payload = {
        "columns": ["name", "close", "open", "high", "low", "volume", "change", "previous_close"],
        "filter": [],
        "options": {"lang": "en"},
        "range": [0, 50],
        "sort": {"sortBy": "volume", "sortOrder": "desc"},
        "symbols": {"query": {"types": ["stock"]}, "tickers": tv_symbols},
        "markets": ["egypt"],
    }

    try:
        resp = httpx.post(_TV_SCAN_URL, json=payload, headers=_TV_HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning("TradingView scanner returned %d", resp.status_code)
            return _TV_CACHE

        data = resp.json()
        result = {}
        for item in data.get("data", []):
            d = item.get("d", [])
            if len(d) < 8:
                continue
            name, close, opn, high, low, vol, chg, prev = d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7]
            if close is None or close <= 0:
                continue
            if prev is None and chg is not None and chg != 0:
                prev = round(close / (1 + chg / 100), 2)
            result[name] = {
                "close": close,
                "open": opn or close,
                "high": high or close,
                "low": low or close,
                "volume": vol or 0,
                "change_pct": chg,
                "previous_close": prev,
                "_ts": now,
            }

        _TV_CACHE.clear()
        _TV_CACHE.update(result)
        logger.info("TradingView: fetched %d EGX stocks", len(result))
        return _TV_CACHE

    except Exception as e:
        logger.warning("TradingView batch fetch failed: %s", e)
        return _TV_CACHE


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

    if _is_market_hours() and not data.empty:
        tv = _tv_batch_fetch()
        tv_data = tv.get(ticker)
        if tv_data and tv_data.get("close"):
            today = datetime.now(_CAIRO_TZ).date()
            last_bar_date = data.index[-1].date() if hasattr(data.index[-1], 'date') else None
            if last_bar_date == today:
                data.iloc[-1, data.columns.get_loc("Open")] = tv_data["open"]
                data.iloc[-1, data.columns.get_loc("High")] = tv_data["high"]
                data.iloc[-1, data.columns.get_loc("Low")] = tv_data["low"]
                data.iloc[-1, data.columns.get_loc("Close")] = tv_data["close"]
                data.iloc[-1, data.columns.get_loc("Volume")] = tv_data["volume"]
                logger.debug("%s: updated last bar with TradingView data (close=%.2f)", ticker, tv_data["close"])
            else:
                new_row = pd.DataFrame(
                    {
                        "Open": [tv_data["open"]],
                        "High": [tv_data["high"]],
                        "Low": [tv_data["low"]],
                        "Close": [tv_data["close"]],
                        "Volume": [tv_data["volume"]],
                    },
                    index=pd.DatetimeIndex([pd.Timestamp(today, tz=_CAIRO_TZ)]),
                )
                data = pd.concat([data, new_row])
                logger.debug("%s: appended TradingView bar (close=%.2f)", ticker, tv_data["close"])

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

    # ── Source 0: TradingView (real-time during market hours) ────────
    if _is_market_hours():
        try:
            tv = _tv_batch_fetch()
            tv_data = tv.get(ticker)
            if tv_data and tv_data.get("close") and tv_data["close"] > 0:
                result["last_traded_price"] = round(tv_data["close"], 2)
                result["session_open"] = round(tv_data["open"], 2)
                result["session_high"] = round(tv_data["high"], 2)
                result["session_low"] = round(tv_data["low"], 2)
                result["previous_close"] = round(tv_data["previous_close"], 2) if tv_data.get("previous_close") else None
                result["price_type"] = "LAST_TRADE"
                result["source"] = "tradingview"
                return result
        except Exception as e:
            logger.debug("TradingView quote failed for %s: %s", ticker, e)

    yahoo_sym = to_yahoo(ticker)
    if not yahoo_sym:
        return _empty_quote(ticker, "INVALID_TICKER")

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
