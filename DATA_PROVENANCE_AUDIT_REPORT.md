# Data Provenance Audit Report

**Date:** 2026-07-22 (Wednesday) — MARKET_OPEN hours  
**Audit tool:** `scanner/audit_data_provenance.py`  
**Context:** Production `DAILY_COMPLETED_SESSION` mode, all34 EGX Shariah stocks  

---

## Executive Summary

During MARKET_OPEN, the radar scoring pipeline uses a **split-provider data model**. Every stock's activity score is built from two different sources blended in `get_completed_daily_bars()`:

| Component | Provider | Session |
|---|---|---|
| Latest bar (OHLCV) | **TradingView** scanner API | 2026-07-22 (partial/live) |
| Previous bars (OHLCV) | **Yahoo Finance** | up to 2026-07-21 (last completed) |
| RVOL denominator (avg vol 20) | Yahoo's 20 historical bars only | last 20 completed sessions |
| RSI / MACD / EMA | Calculated over **close series including partial bar** | mixed |
| `source` field on all bars | **Hardcoded "yfinance"** | always — provenance lost |

**Every stock** during MARKET_OPEN is flagged `MIXED_PROVIDERS: close=TV, prev_close=Yahoo`.  
**Every stock** has `INDICATORS_INCLUDE_PARTIAL_BAR = YES`.  
**Every stock** has `PARTIAL_SESSION_BAR = YES`.  
**Every stock** has at least one `DATA_GAP` entry.

---

## Q1: Where does today's close price come from in the radar score?

**Answer:** TradingView's scanner API during MARKET_OPEN.

- `fetch_live_quote()` checks `_is_market_hours()` → returns True between 09:00–14:45 Cairo.
- During market hours, it calls `_tv_live_quote()` first (`scanner/data_provider.py:102–117`).
- The returned `{"close": ..., "volume": ...}` is used for today's partial session.
- `prev_close` comes from the **previous Yahoo bar** (2026-07-21).
- Both are passed to `_analyze_symbol()` in `market_radar.py:122–123`.

**Audit evidence (LCSW):**
```
prev_close.yahoo_date = 2026-07-21,  prev_close.yahoo_value = 35.00
latest_bar.session    = 2026-07-22 (partial)
close.source          = TradingView
```

---

## Q2: Where does the volume number in RVOL come from?

**Answer:** Numerator = TradingView's `volume` field. Denominator = Yahoo's historical average.

- `avg_vol_20` is computed from Yahoo's 20 most recent **completed** bars only (`radar_data.py:318–338`).
- `rvol = latest_volume / avg_vol_20` (`market_radar.py:344–347`).
- During MARKET_OPEN the latest bar has TradingView volume; the historical average uses Yahoo bars.

**Audit evidence (LCSW):**
```
avg_vol_20YahooBars = 20,  from_sessions = 2026-06-21→2026-07-20
rvolYahooBars = 20,  rvol_raw = 0.95
```

Note: `rvolYahooBars` equals `avg_vol_20YahooBars` because TradingView appends 1 bar on top of Yahoo's 20 → total bars = 21, and the rvol calculation uses the first 20 (Yahoo) bars for the average.

---

## Q3: When RSI and MACD are calculated, do they include the partial session bar?

**Answer:** Yes. Every indicator includes the partial session bar.

- `get_completed_daily_bars()` returns TradingView's 2026-07-22 bar as the last entry.
- `_analyze_symbol()` builds `df['close']` from `bar.close for bar in all_bars` (`market_radar.py:306`).
- The partial bar's close is appended to the series before `compute_indicators()`.
- RSI (14) and MACD are computed over this blended series.

**Audit evidence (LCSW):**
```
INDICATORS_INCLUDE_PARTIAL_BAR = YES  (2026-07-22 partial)
bars_totalYahooBars = 20 + 1 TV = 21
```

**Impact:** RSI is calculated over 21 data points (20 full + 1 partial). The partial bar's close is a live snapshot — the value may shift as the session continues. Indicators will stabilize after market close when the partial bar is replaced with a confirmed completed bar.

---

## Q4: Is the radar actually showing live data, or is it still using yesterday's close?

**Answer:** Both, simultaneously.

The radar presents:
- **Today's live snapshot** (TradingView) as the "Close" value.
- **Yesterday's confirmed close** (Yahoo) as the "Previous Close".
- **RVOL** computed as today's live volume / 20-day average of Yahoo volumes.
- **RSI/MACD** computed over a series that includes today's live snapshot.

During MARKET_OPEN the date shown is `2026-07-22` (today), not `2026-07-21` (last completed).  
After market close, the pipeline switches to `DAILY_COMPLETED_SESSION` mode, fetches only Yahoo bars, and the latest bar becomes `2026-07-21` (confirmed completed).

---

## Q5: If two providers disagree on the same bar's close, which wins?

**Answer:** They never disagree on the same bar, because they cover different sessions.

- Yahoo bars cover 2026-06-21 through 2026-07-21 (completed sessions).
- TradingView appends 2026-07-22 (today's partial session).
- There is no session overlap — TradingView's bar is appended, not substituted.

**However**, `fetch_history()` does have a deduplication/replace check:
```python
if existing_date == new_date:          # same session
    if not existing_bar.is_complete:    # Yahoo stub
        bars[idx] = tv_bar              # TV replaces Yahoo
```

In production this path was not exercised on 2026-07-22 because Yahoo has no 2026-07-22 bar.

**Consistency risk:** If Yahoo ever returns a stub bar for today (close=0, volume=0), TradingView's bar will replace it in `fetch_history()`. The `source` field will still say "yfinance" due to the provenance bug.

---

## Q6: How many bars come from Yahoo vs TradingView for the most active stock (ACGC)?

**Answer:** 20 Yahoo bars + 1 TradingView bar.

```
ACGC:
  Yahoo: 20 bars (2026-06-18 → 2026-07-21)
  TV:     1 bar  (2026-07-22 partial, is_complete=True)
  total: 21 bars
  latest_bar_is_partial = YES
  prev_close.yahoo_date = 2026-07-21,  prev_close.yahoo_value = 84.57
  rvolYahooBars = 20,  avg_vol_20YahooBars = 20
```

The TradingView bar is appended at `radar_data.py:421–437`. It is the 21st bar in the list passed to `_analyze_symbol()`.

---

## Q7: If TradingView data has a glitch, does the radar crash or show stale data?

**Answer:** Neither. It shows stale Yahoo data silently.

`_tv_batch_fetch()` catches all exceptions and returns `None` per ticker (`data_provider.py:84–87`):
```python
except Exception as e:
    if log_failures:
        logger.warning("TradingView batch failed: %s", e)
    return None
```

When `None` is returned:
- `fetch_history()` skips the overlay entirely — no TradingView bar appended.
- `fetch_live_quote()` falls through to Yahoo's `fast_info` (`data_provider.py:143–150`).
- The latest bar becomes Yahoo's last completed session (e.g. 2026-07-21).
- RVOL is computed entirely from Yahoo data — no partial session noise.

**This is the correct degraded behavior.** The radar shows last completed session data, not live data, until TradingView recovers.

---

## Consistency Flags

| Flag | Stocks | Explanation |
|---|---|---|
| `PARTIAL_SESSION_BAR` | ALL 34 | Latest bar is 2026-07-22 (partial) |
| `MIXED_PROVIDERS` | ALL 34 | Close from TradingView, prev_close from Yahoo |
| `LIVE_VOLUME` | ALL 34 | RVOL numerator uses TradingView's partial-session volume |
| `INDICATORS_INCLUDE_PARTIAL_BAR` | ALL 34 | RSI/MACD computed with partial bar in series |
| `DATA_GAP` | ALL 34 | No Yahoo bar for 2026-07-22 (expected during market hours) |
| `CORRUPTED_YAHOO` | ORAS only | 10/10 recent Yahoo bars have Volume=0 |

### ORAS — Corrupted Yahoo Data

ORAS shows `Close=71.05, Volume=0` for all recent Yahoo bars. TradingView overlay appends `714.50` with `Volume=1,116,000`. This creates an artificial +905.6% RVOL spike and a fake EXTREME BUYING_ACTIVITY signal.

**Root cause:** Yahoo Finance returns Volume=0 for ORAS. The `_bars_are_valid()` filter does not catch this because it only checks OHLC consistency, not zero volume.

**Fix required:** Add a zero-volume guard in `_bars_are_valid()` or in `_tv_batch_fetch()` before the TradingView overlay.

---

## Per-Field Source Map (LCSW — representative)

| Field | Value | Session | Source | Notes |
|---|---|---|---|---|
| `close` (latest) | 33.83 | 2026-07-22 | TradingView | partial session bar |
| `prev_close` | 35.00 | 2026-07-21 | Yahoo | last completed |
| `open` (latest) | 35.00 | 2026-07-22 | TradingView | |
| `high` (latest) | 35.00 | 2026-07-22 | TradingView | |
| `low` (latest) | 33.50 | 2026-07-22 | TradingView | |
| `volume` (latest) | 2,062,457 | 2026-07-22 | TradingView | partial session |
| `avg_vol_20` | ~2,169,954 | 20 bars | Yahoo only | bars 2026-06-21 → 2026-07-20 |
| `rvol` | 0.95x | — | calculated | TV vol / Yahoo avg |
| `rsi` | ~30–50 | 21 bars | calculated | 20 Yahoo + 1 TV partial |
| `macd` | — | 21 bars | calculated | same blended series |
| `source` (all bars) | "yfinance" | — | **hardcoded** | provenance lost — bug |

---

## Recommendations

1. **Fix `DailyBar.source`** — currently hardcoded to `"yfinance"` at `radar_data.py:414`. Should reflect actual provider (`"yfinance"`, `"tradingview"`, `"egxapi"`).
2. **Add zero-volume guard** — `_bars_are_valid()` should reject bars where `volume == 0` to prevent TradingView overlay from creating fake signals (ORAS case).
3. **Document the split-provider model** — the radar documentation should explicitly state that during MARKET_OPEN, OHLCV comes from TradingView while historical context comes from Yahoo.
4. **Consider storing `prev_close` source** — the `prev_close` dict does not track which provider provided it; adding a `source` field would improve traceability.
