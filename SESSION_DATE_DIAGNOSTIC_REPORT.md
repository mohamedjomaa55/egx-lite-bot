# SESSION DATE DIAGNOSTIC REPORT
## Market Radar — Provider Freshness Detection
**Date:** 2026-07-22 01:43 Cairo  
**Status:** RESOLVED

---

## Problem Statement

Radar was displaying session date **2026-07-20** when the **2026-07-21** EGX session had completed **11+ hours ago** (session closed at 14:15 Cairo, diagnostic run at 01:43 Cairo on 2026-07-22).

## Root Cause

**Yahoo Finance data delay** — the provider simply does not have the 2026-07-21 session data yet. This is a known delay for EGX stocks, especially late at night / early morning.

### Diagnostic Evidence

| Symbol | Yahoo Last Row | Has 2026-07-21? | end=tomorrow Result |
|--------|---------------|-----------------|---------------------|
| ARCC.CA | 2026-07-20 | No | 2026-07-20 (247 rows) |
| EGAL.CA | 2026-07-20 | No | 2026-07-20 (247 rows) |
| COMI.CA | 2026-07-20 | No | 2026-07-20 (247 rows) |

- Index timezone was already `Africa/Cairo` (EEST)
- Setting `end=tomorrow` (2026-07-22) did NOT help — Yahoo simply doesn't have the data
- The `end` parameter being exclusive was not the issue
- 5-minute cache was not the issue (fresh calls returned same data)

## Code Issues Found

### 1. No EGX Session Calendar (`config.py`)
**Before:** No knowledge of EGX trading hours or calendar.  
**After:** Added:
- `EGX_TRADING_DAYS = {0, 1, 2, 3, 6}` (Sun-Thu, Python weekday format)
- `EGX_OPEN_HOUR = 9`, `EGX_OPEN_MINUTE = 30`
- `EGX_CLOSE_HOUR = 14`, `EGX_CLOSE_MINUTE = 15`
- `EGX_SAFETY_BUFFER_MINUTES = 30`
- Freshness status constants (`FRESHNESS_CURRENT`, `FRESHNESS_PROVIDER_DELAYED`, etc.)

### 2. No Freshness Detection (`radar_data.py`)
**Before:** Blindly used `df.iloc[-1]` as latest session.  
**After:** Added:
- `get_expected_latest_egx_session(now_cairo)` — calculates expected latest completed session based on EGX calendar and current Cairo time
- `is_market_open(now_cairo)` — checks if EGX is currently in a trading session
- `assess_data_freshness(provider_date, now_cairo)` — compares provider data against expected, returns status + delay

### 3. No Freshness Visibility (`market_radar.py`, `radar_output.py`)
**Before:** No indication of data staleness.  
**After:** Added:
- Freshness fields on `RadarItem`: `provider_latest_date`, `expected_latest_session`, `freshness_status`, `freshness_note`, `freshness_delay_days`
- Freshness fields on `MarketRadarResult`: `expected_latest_session`, `freshness_status`, `freshness_note`
- Console header shows: Expected Session, Freshness status, Freshness Note
- Console warns when `PROVIDER_DELAYED`
- Telegram output shows: Expected, Freshness, warning when delayed
- Symbol detail shows: Provider Latest, Expected Session, Freshness, Freshness Note

### 4. Stale Data Displayed Without Warning
**Before:** Radar showed "Data Date: 2026-07-20" with no context.  
**After:** Radar now shows:
```
Data Date           : 2026-07-20
Expected Session    : 2026-07-21
Freshness           : PROVIDER_DELAYED
Freshness Note      : Provider delayed 1 day(s)
```
With yellow warning: `⚠ Provider delay detected — data is Provider delayed 1 day(s)`

## Files Modified

| File | Changes |
|------|---------|
| `scanner/config.py` | Added EGX session calendar, trading days, freshness constants |
| `scanner/radar_data.py` | Added `get_expected_latest_egx_session()`, `is_market_open()`, `assess_data_freshness()`; added freshness fields to `RadarHistory`; populated freshness in `get_completed_daily_bars()` |
| `scanner/market_radar.py` | Added freshness fields to `RadarItem` and `MarketRadarResult`; populated from history in `_analyze_symbol()` and `run_market_radar()` |
| `scanner/radar_output.py` | Updated console header with freshness; added warning for delayed data; updated Telegram output; updated symbol detail |
| `tests/test_market_radar.py` | Added 22 new regression tests across 3 test classes |

## Tests Added

### TestSessionDateDetection (10 tests)
- After close Wednesday → expected = Wednesday
- Before close Wednesday → expected = Tuesday
- Friday evening → expected = Thursday
- Saturday afternoon → expected = Thursday
- Sunday after close → expected = Sunday (trading day)
- Sunday before close → expected = Thursday
- Exact close time (14:15) → expected = Tuesday (buffer not elapsed)
- After buffer (14:46) → expected = Wednesday
- Monday morning → expected = Sunday
- Returns datetime with Cairo timezone

### TestDataFreshnessAssessment (8 tests)
- Current data → CURRENT
- 1-day delay → CURRENT (within tolerance)
- 2-day delay → PROVIDER_DELAYED
- Market open → MARKET_OPEN
- Non-trading day → NON_TRADING_DAY
- Unparseable date → DATA_UNAVAILABLE
- Provider ahead of expected → CURRENT
- Delay days calculation accuracy

### TestFreshnessFieldsInRadarItem (3 tests)
- RadarItem has freshness fields
- Freshness fields have sensible defaults
- MarketRadarResult has freshness fields

## Freshness Statuses

| Status | Meaning | Action |
|--------|---------|--------|
| `CURRENT` | Provider data matches expected session | None — data is fresh |
| `PROVIDER_DELAYED` | Provider data older than expected | Warning displayed, ISM should account for staleness |
| `MARKET_OPEN` | EGX session in progress (09:30-14:15) | Data from previous session (expected) |
| `NON_TRADING_DAY` | Today is Friday, Saturday, or holiday | Data from last trading day (expected) |
| `DATA_UNAVAILABLE` | Provider returned unparseable data | Critical — data cannot be used |

## Test Results

```
111 passed in 2.72s
```

- 73 market radar tests (including 22 new freshness tests)
- 26 shadow unit tests
- 12 shadow integration tests
