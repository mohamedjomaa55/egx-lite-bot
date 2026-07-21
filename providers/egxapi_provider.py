"""
EGXAPI Shadow Provider — Market-Data Evaluation Only
=====================================================

Read-only provider that calls the EGXAPI REST API in paper/sandbox
mode.  It NEVER places orders, NEVER touches live endpoints, and
NEVER replaces the existing Yahoo Finance data path.

Environment variables
---------------------
    EGXAPI_KEY   — API key (paper only).  Never logged or printed.
    EGXAPI_ENV   — "paper" (default) or "live" (read-only still).

Usage
-----
    from providers.egxapi_provider import EGXAPIProvider

    p = EGXAPIProvider()
    q = p.get_quote("ARCC")
    print(q)

Shadow mode
-----------
    The provider is invoked from ``compare_quote`` and from the
    ``/egxapi_status`` Telegram command.  Results are compared
    against the existing ``fetch_live_quote`` output but never
    overwrite it.
"""

from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, NoReturn, Optional

import httpx

# ─── Module-level state ───────────────────────────────────────────────
logger = logging.getLogger("providers.egxapi")

_BASE_URL = "https://api.egxapi.com/v2"
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.5  # seconds, doubled each retry


# ─── States ───────────────────────────────────────────────────────────
class QuoteState(str, Enum):
    LIVE_VERIFIED = "LIVE_VERIFIED"
    DELAYED = "DELAYED"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    INVALID_SYMBOL = "INVALID_SYMBOL"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


# ─── Normalised data classes ──────────────────────────────────────────
@dataclass
class NormalizedQuote:
    symbol: str
    last_price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    previous_close: Optional[float] = None
    volume: Optional[int] = None
    timestamp: Optional[str] = None
    source: str = "egxapi"
    latency_ms: Optional[float] = None
    state: str = QuoteState.DATA_UNAVAILABLE.value
    raw: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


@dataclass
class NormalizedTrade:
    symbol: str
    price: Optional[float] = None
    size: Optional[int] = None
    timestamp: Optional[str] = None
    side: Optional[str] = None
    source: str = "egxapi"
    raw: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


@dataclass
class NormalizedBar:
    symbol: str
    interval: str = ""
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[int] = None
    timestamp: Optional[str] = None
    source: str = "egxapi"
    raw: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


@dataclass
class ComparisonResult:
    symbol: str
    egxapi_price: Optional[float] = None
    fallback_price: Optional[float] = None
    price_difference: Optional[float] = None
    price_difference_percent: Optional[float] = None
    timestamp_age_seconds: Optional[float] = None
    egxapi_state: str = QuoteState.DATA_UNAVAILABLE.value
    egxapi_timestamp: Optional[str] = None
    egxapi_bid: Optional[float] = None
    egxapi_ask: Optional[float] = None
    egxapi_volume: Optional[int] = None
    fallback_source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Provider ─────────────────────────────────────────────────────────
class EGXAPIProvider:
    """
    Isolated, read-only EGXAPI client.

    Parameters
    ----------
    api_key : str | None
        Override for env-based key.  If None, reads EGXAPI_KEY.
    env : str | None
        Override for env.  If None, reads EGXAPI_ENV (default "paper").
    timeout : float
        HTTP timeout in seconds.
    """

    def __init__(
        self,
        api_key: str | None = None,
        env: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._api_key = api_key or os.getenv("EGXAPI_KEY", "")
        self._env = (env or os.getenv("EGXAPI_ENV", "paper")).lower()
        self._timeout = timeout
        self._client: httpx.Client | None = None
        self._available: bool | None = None  # None = untested

        if not self._api_key:
            logger.warning(
                "EGXAPI_KEY not set — provider will return DATA_UNAVAILABLE"
            )

    # ── Lifecycle ─────────────────────────────────────────────────────
    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=_BASE_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "X-EGX-Env": self._env,
                    "Accept": "application/json",
                },
                timeout=self._timeout,
            )
        return self._client

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()

    # ── HTTP helpers ──────────────────────────────────────────────────
    def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict | list | None:
        """
        Fire an HTTP request with retry + backoff.

        Returns parsed JSON on 2xx, None on failure.
        Never raises — errors are logged and returned as None.
        """
        if not self._api_key:
            logger.debug("No EGXAPI_KEY — skipping request %s %s", method, path)
            return None

        client = self._get_client()
        last_err: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            t0 = time.monotonic()
            try:
                resp = client.request(method, path, **kwargs)
                latency = (time.monotonic() - t0) * 1000
                logger.debug(
                    "EGXAPI %s %s → %s (%.0f ms, attempt %d)",
                    method, path, resp.status_code, latency, attempt,
                )

                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code == 404:
                    logger.info("EGXAPI 404 for %s %s", method, path)
                    return None

                if resp.status_code == 429:
                    wait = _RETRY_BACKOFF * (2 ** (attempt - 1))
                    logger.warning(
                        "EGXAPI 429 rate-limited, waiting %.1fs", wait
                    )
                    time.sleep(wait)
                    continue

                if resp.status_code >= 500:
                    wait = _RETRY_BACKOFF * (2 ** (attempt - 1))
                    logger.warning(
                        "EGXAPI %d server error, retry in %.1fs",
                        resp.status_code, wait,
                    )
                    time.sleep(wait)
                    continue

                # 4xx other than 404/429 — don't retry
                logger.warning(
                    "EGXAPI %d for %s %s: %s",
                    resp.status_code, method, path,
                    resp.text[:200],
                )
                return None

            except httpx.TimeoutException as exc:
                last_err = exc
                wait = _RETRY_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    "EGXAPI timeout on %s %s (attempt %d), retry in %.1fs",
                    method, path, attempt, wait,
                )
                time.sleep(wait)

            except httpx.HTTPError as exc:
                last_err = exc
                logger.error(
                    "EGXAPI HTTP error on %s %s: %s", method, path, exc
                )
                return None

        if last_err:
            logger.error(
                "EGXAPI exhausted %d retries for %s %s: %s",
                _MAX_RETRIES, method, path, last_err,
            )
        return None

    # ── Public: get_quote ─────────────────────────────────────────────
    def get_quote(self, symbol: str) -> NormalizedQuote:
        """
        Fetch the latest quote for *symbol* from EGXAPI.

        Parameters
        ----------
        symbol : str
            EGX ticker, e.g. "ARCC", "COMI".

        Returns
        -------
        NormalizedQuote
            Always returned; check ``state`` for validity.
        """
        t0 = time.monotonic()
        symbol = symbol.strip().upper()

        data = self._request("GET", f"/quotes/{symbol}")
        latency = (time.monotonic() - t0) * 1000

        if data is None:
            self._available = False
            return NormalizedQuote(
                symbol=symbol,
                latency_ms=round(latency, 1),
                state=QuoteState.DATA_UNAVAILABLE.value,
            )

        # Try to detect whether the response is a valid quote object.
        # EGXAPI may return {"data": {...}} or the quote directly.
        q = data if isinstance(data, dict) and "symbol" in data else data.get("data", data) if isinstance(data, dict) else data

        if not isinstance(q, dict):
            self._available = False
            return NormalizedQuote(
                symbol=symbol,
                latency_ms=round(latency, 1),
                state=QuoteState.DATA_UNAVAILABLE.value,
                raw=data if isinstance(data, dict) else {},
            )

        self._available = True

        # ── Extract fields (tolerant of naming variants) ──────────────
        def _f(*keys: str) -> Optional[float]:
            for k in keys:
                v = q.get(k)
                if v is not None:
                    try:
                        return round(float(v), 4)
                    except (ValueError, TypeError):
                        continue
            return None

        def _i(*keys: str) -> Optional[int]:
            for k in keys:
                v = q.get(k)
                if v is not None:
                    try:
                        return int(v)
                    except (ValueError, TypeError):
                        continue
            return None

        def _s(*keys: str) -> Optional[str]:
            for k in keys:
                v = q.get(k)
                if v is not None:
                    return str(v)
            return None

        last_price = _f("lastPrice", "last_price", "close", "price")
        bid = _f("bid", "bidPrice", "bid_price")
        ask = _f("ask", "askPrice", "ask_price")
        opn = _f("open", "openPrice", "open_price")
        high = _f("high", "dayHigh", "day_high")
        low = _f("low", "dayLow", "day_low")
        prev_close = _f("previousClose", "previous_close", "prevClose")
        volume = _i("volume", "totalVolume", "total_volume")
        ts = _s("timestamp", "lastTradeTime", "last_trade_time", "time")

        # ── Determine state ───────────────────────────────────────────
        state = QuoteState.LIVE_VERIFIED.value
        if last_price is None:
            state = QuoteState.DATA_UNAVAILABLE.value

        quote = NormalizedQuote(
            symbol=symbol,
            last_price=last_price,
            bid=bid,
            ask=ask,
            open=opn,
            high=high,
            low=low,
            previous_close=prev_close,
            volume=volume,
            timestamp=ts,
            source="egxapi",
            latency_ms=round(latency, 1),
            state=state,
            raw=q,
        )

        logger.info(
            "EGXAPI quote %s: price=%s state=%s latency=%.0fms",
            symbol, last_price, state, latency,
        )
        return quote

    # ── Public: get_trades ────────────────────────────────────────────
    def get_trades(self, symbol: str, limit: int = 50) -> list[NormalizedTrade]:
        """
        Fetch recent trades for *symbol*.

        Parameters
        ----------
        symbol : str
            EGX ticker.
        limit : int
            Max trades to return (default 50).

        Returns
        -------
        list[NormalizedTrade]
        """
        symbol = symbol.strip().upper()
        data = self._request("GET", f"/trades/{symbol}", params={"limit": limit})

        if data is None:
            return []

        # Unwrap if nested under "data"
        items = data
        if isinstance(data, dict):
            items = data.get("data", data.get("trades", []))
        if not isinstance(items, list):
            items = []

        results: list[NormalizedTrade] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            results.append(
                NormalizedTrade(
                    symbol=symbol,
                    price=_safe_float(item, "price", "lastPrice"),
                    size=_safe_int(item, "size", "volume", "qty"),
                    timestamp=_safe_str(item, "timestamp", "time"),
                    side=_safe_str(item, "side", "direction"),
                    source="egxapi",
                    raw=item,
                )
            )

        logger.info("EGXAPI trades %s: %d trades returned", symbol, len(results))
        return results

    # ── Public: get_intraday_bars ─────────────────────────────────────
    def get_intraday_bars(
        self,
        symbol: str,
        interval: str = "1m",
        limit: int = 100,
    ) -> list[NormalizedBar]:
        """
        Fetch intraday OHLCV bars for *symbol*.

        Parameters
        ----------
        symbol : str
            EGX ticker.
        interval : str
            Bar interval: "1m", "5m", "15m", "30m", "1h".
        limit : int
            Max bars to return (default 100).

        Returns
        -------
        list[NormalizedBar]
        """
        symbol = symbol.strip().upper()
        data = self._request(
            "GET",
            f"/bars/{symbol}",
            params={"interval": interval, "limit": limit},
        )

        if data is None:
            return []

        items = data
        if isinstance(data, dict):
            items = data.get("data", data.get("bars", []))
        if not isinstance(items, list):
            items = []

        results: list[NormalizedBar] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            results.append(
                NormalizedBar(
                    symbol=symbol,
                    interval=interval,
                    open=_safe_float(item, "open"),
                    high=_safe_float(item, "high"),
                    low=_safe_float(item, "low"),
                    close=_safe_float(item, "close"),
                    volume=_safe_int(item, "volume"),
                    timestamp=_safe_str(item, "timestamp", "time"),
                    source="egxapi",
                    raw=item,
                )
            )

        logger.info(
            "EGXAPI bars %s (%s): %d bars returned", symbol, interval, len(results)
        )
        return results

    # ── Public: compare_quote ─────────────────────────────────────────
    def compare_quote(
        self,
        symbol: str,
        manual_reference: Optional[float] = None,
    ) -> ComparisonResult:
        """
        Compare EGXAPI quote against a reference price.

        The reference can be:
          - ``manual_reference`` (explicit float), OR
          - the current fallback price from ``fetch_live_quote``.

        Parameters
        ----------
        symbol : str
            EGX ticker.
        manual_reference : float | None
            If provided, used as the reference price.
            Otherwise falls back to Yahoo Finance ``fetch_live_quote``.

        Returns
        -------
        ComparisonResult
        """
        symbol = symbol.strip().upper()

        # ── Get EGXAPI price ──────────────────────────────────────────
        egxapi_q = self.get_quote(symbol)
        egxapi_price = egxapi_q.last_price

        # ── Get fallback price ────────────────────────────────────────
        fallback_price = manual_reference
        fallback_source = "manual_reference"
        if fallback_price is None:
            try:
                from scanner.data_provider import fetch_live_quote
                fb = fetch_live_quote(symbol)
                fallback_price = fb.get("last_traded_price")
                fallback_source = fb.get("source", "yfinance")
            except Exception as exc:
                logger.debug("Fallback fetch failed for %s: %s", symbol, exc)
                fallback_source = "unavailable"

        # ── Calculate differences ─────────────────────────────────────
        price_diff = None
        price_diff_pct = None
        if egxapi_price is not None and fallback_price is not None:
            price_diff = round(egxapi_price - fallback_price, 4)
            if fallback_price != 0:
                price_diff_pct = round(
                    (price_diff / abs(fallback_price)) * 100, 4
                )

        # ── Timestamp age ─────────────────────────────────────────────
        ts_age = None
        if egxapi_q.timestamp:
            try:
                # Try ISO format
                ts_dt = datetime.fromisoformat(
                    egxapi_q.timestamp.replace("Z", "+00:00")
                )
                ts_age = (datetime.now(timezone.utc) - ts_dt).total_seconds()
            except (ValueError, TypeError):
                pass

        # ── Determine overall state ───────────────────────────────────
        state = egxapi_q.state
        if (
            state == QuoteState.LIVE_VERIFIED.value
            and price_diff is not None
            and abs(price_diff_pct or 0) > 5.0
        ):
            state = QuoteState.PRICE_MISMATCH.value

        result = ComparisonResult(
            symbol=symbol,
            egxapi_price=egxapi_price,
            fallback_price=fallback_price,
            price_difference=price_diff,
            price_difference_percent=price_diff_pct,
            timestamp_age_seconds=round(ts_age, 1) if ts_age is not None else None,
            egxapi_state=state,
            egxapi_timestamp=egxapi_q.timestamp,
            egxapi_bid=egxapi_q.bid,
            egxapi_ask=egxapi_q.ask,
            egxapi_volume=egxapi_q.volume,
            fallback_source=fallback_source,
        )

        logger.info(
            "EGXAPI compare %s: egxapi=%s fallback=%s diff=%s%% state=%s",
            symbol, egxapi_price, fallback_price,
            f"{price_diff_pct:.2f}" if price_diff_pct is not None else "N/A",
            state,
        )
        return result

    # ── Availability probe ────────────────────────────────────────────
    def is_available(self) -> bool:
        """
        Quick probe: can we reach the EGXAPI and get a valid response?

        Returns True only after a successful request.  Caches result
        until the provider instance is recreated.
        """
        if self._available is not None:
            return self._available

        if not self._api_key:
            self._available = False
            return False

        try:
            resp = self._get_client().get("/account")
            self._available = resp.status_code == 200
        except Exception:
            self._available = False

        return self._available

    # ── Blocked order functions ───────────────────────────────────────
    def create_order(self, *a: Any, **kw: Any) -> NoReturn:
        raise NotImplementedError(
            "EGXAPI shadow provider — order creation is blocked. "
            "Use the official SDK directly for live trading."
        )

    def cancel_order(self, *a: Any, **kw: Any) -> NoReturn:
        raise NotImplementedError(
            "EGXAPI shadow provider — order operations are blocked."
        )

    def get_orders(self, *a: Any, **kw: Any) -> NoReturn:
        raise NotImplementedError(
            "EGXAPI shadow provider — order operations are blocked."
        )

    def get_positions(self, *a: Any, **kw: Any) -> NoReturn:
        raise NotImplementedError(
            "EGXAPI shadow provider — position queries are blocked."
        )


# ─── Module-level convenience ─────────────────────────────────────────
_default: EGXAPIProvider | None = None


def get_provider() -> EGXAPIProvider:
    """Return (and lazily create) the module-level singleton."""
    global _default
    if _default is None:
        _default = EGXAPIProvider()
    return _default


def get_quote(symbol: str) -> NormalizedQuote:
    """Shortcut for ``get_provider().get_quote(symbol)``."""
    return get_provider().get_quote(symbol)


def get_trades(symbol: str, limit: int = 50) -> list[NormalizedTrade]:
    """Shortcut for ``get_provider().get_trades(symbol, limit)``."""
    return get_provider().get_trades(symbol, limit)


def get_intraday_bars(
    symbol: str, interval: str = "1m", limit: int = 100
) -> list[NormalizedBar]:
    """Shortcut for ``get_provider().get_intraday_bars(symbol, interval, limit)``."""
    return get_provider().get_intraday_bars(symbol, interval, limit)


def compare_quote(
    symbol: str, manual_reference: float | None = None
) -> ComparisonResult:
    """Shortcut for ``get_provider().compare_quote(symbol, manual_reference)``."""
    return get_provider().compare_quote(symbol, manual_reference)


# ─── Tiny helpers ─────────────────────────────────────────────────────
def _safe_float(d: dict, *keys: str) -> Optional[float]:
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return round(float(v), 4)
            except (ValueError, TypeError):
                continue
    return None


def _safe_int(d: dict, *keys: str) -> Optional[int]:
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return int(v)
            except (ValueError, TypeError):
                continue
    return None


def _safe_str(d: dict, *keys: str) -> Optional[str]:
    for k in keys:
        v = d.get(k)
        if v is not None:
            return str(v)
    return None
