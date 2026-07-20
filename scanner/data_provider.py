import yfinance as yf
import pandas as pd
import time
import logging
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


def fetch_index() -> pd.DataFrame:
    """Fetch the EGX30 index data for market filter."""
    return fetch_history(config.EGX_INDEX)


def get_all_tickers() -> list[str]:
    return sorted(config.EGX_SYMBOL_MAP.keys())
