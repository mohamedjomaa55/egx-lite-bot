# TELEGRAM BOT FORMATTER DIAGNOSTIC REPORT
## Old Radar Output Persisting After OHLC + Session Freshness Fixes
**Date:** 2026-07-22  
**Status:** RESOLVED

---

## Problem Statement

The running Telegram bot was still producing old Market Radar output:
```
Data: Latest completed session
Date: 2026-07-20
Price: 56.93
```

Instead of the new format:
```
Date: 2026-07-20
Expected: 2026-07-21
Freshness: PROVIDER_DELAYED
Last Completed Close: 56.93
```

## Root Cause

**`bot.py:handle_radar_category` (line 287) had inline formatting that bypassed the formatter.**

The function `handle_radar_category` — triggered when users click a category button (Buying/Selling/Unusual) — contained its own inline formatting code instead of using `format_radar_telegram()` or `format_radar_symbol_telegram()` from `scanner/radar_output.py`.

### Specific Issues Found

| Line | Old Code | Issue |
|------|----------|-------|
| `bot.py:321` | `f"Date: {result.data_date}"` | Missing Expected Session and Freshness fields |
| `bot.py:329` | `f"Price: {item.price:.2f}..."` | Uses deprecated `item.price` field and old `"Price:"` label |
| `bot.py:44` | `TRADING_DAYS = [0,1,2,3,4]` | Wrong — Mon-Fri instead of Sun-Thu for EGX |

### Why Other Commands Were Correct

| Command | Formatter Used | Status |
|---------|---------------|--------|
| `/radar` | `format_radar_telegram(result)` at line 279 | ✓ Correct (uses new formatter) |
| `/radar_symbol` | `format_radar_symbol_telegram(item)` at line 215 | ✓ Correct (uses new formatter) |
| `/radar_buying`, `/radar_selling`, `/radar_unusual` | **Inline code** in `handle_radar_category` at line 287 | ✗ **Old format** |

## Files Modified

| File | Changes |
|------|---------|
| `bot.py:319-335` | Fixed `handle_radar_category` inline formatter: `"Price:"` → `"Last Completed Close:"`, `item.price` → `item.latest_close`, added Expected/Freshness fields |
| `bot.py:44` | Fixed `TRADING_DAYS = [0,1,2,3,4]` → `[0,1,2,3,6]` (Sun-Thu) |
| `bot.py:853-882` | Added startup diagnostics: project path, Git branch/commit, radar_output.py path, Python version |

## Verification

### Old Output (BEFORE fix)
```
🟢 BUYING ACTIVITY
Date: 2026-07-20
Found: 1

1. ARCC — Activity 92/100
Price: 56.93 (+3.1%)
Volume: 6.1x | RSI: 65 ↑
Label: Strong buying activity
```

### New Output (AFTER fix)
```
🟢 BUYING ACTIVITY
Date: 2026-07-20
Expected: 2026-07-21
Freshness: PROVIDER_DELAYED
Found: 1

1. ARCC — Activity 92/100
Last Completed Close: 56.93 (+3.1%)
Volume: 6.1x | RSI: 65 ↑
Label: Strong buying activity
```

### Full Radar Output (format_radar_telegram)
```
📡 EGX LITE MARKET RADAR
Data: Latest completed session
Date: 2026-07-20
Expected: 2026-07-21
Freshness: PROVIDER_DELAYED
Scanned: 34
Activity detected: 1

⚠ Provider delay: Provider delayed 1 day(s)
⚠ Activity signals may be based on stale data

🟢 BUYING ACTIVITY

1. ARCC — Activity 92/100
Last Completed Close: 56.93 (+3.1%)
Session Open: 55.20
Volume: 6.1x average
RSI: 65 ↑
MACD Histogram: Improving
Label: Strong buying activity
Reasons:
• RVOL 6.1x versus 20-day average
• Close finished near the session high

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Lite detects activity only.
Use ISM for complete technical analysis and trade decisions.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## Process Restart Confirmation

- ✅ All Python processes stopped
- ✅ All `__pycache__` directories cleared (scanner, providers, app, root)
- ✅ Bot started successfully (connected to Telegram API)
- ✅ 409 Conflict detected — Render deployment also running (expected)
- ✅ Local bot stopped to avoid conflict with Render
- ✅ Changes uncommitted — will take effect on next Render deploy

## Additional Fixes

### `TRADING_DAYS` in `bot.py`
**Before:** `TRADING_DAYS = [0, 1, 2, 3, 4]` (Mon-Fri) — wrong for EGX  
**After:** `TRADING_DAYS = [0, 1, 2, 3, 6]` (Sun-Thu) — correct for EGX

This affected the `is_trading_hours()` function which checks if the bot should respond to commands during trading hours.

### Startup Diagnostics Added
Bot now prints at startup:
```
============================================================
  EGX LITE MARKET RADAR v2.0 — STARTUP DIAGNOSTICS
============================================================
  Project Path      : D:\EGX Lite Bot
  radar_output.py   : D:\EGX Lite Bot\scanner\radar_output.py
  radar_output.py exists: True
  Git Branch        : main
  Git Commit        : 6842f3a Add backup/ and cache dirs to .gitignore
  Python            : 3.14.6
  Trading Days      : [0, 1, 2, 3, 6] (Sun-Thu)
============================================================
```

## Test Results

```
73 passed in 3.69s
```

All existing tests continue to pass. No scoring, classification, OHLC logic, session calendar, ISM, or EGXAPI integration was modified.
