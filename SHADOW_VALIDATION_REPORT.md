# Shadow Validation Report — EGX Swing Scout v1.0

**Generated:** 2026-07-21
**Status:** Ready for live validation (pending EGXAPI key)

---

## Executive Summary

The Shadow Provider module provides parallel price validation between Yahoo Finance (existing provider) and EGXAPI (new provider). All comparison logic, CSV logging, and summary generation are fully implemented and **tested with 38/38 unit and integration tests passing**.

Live validation requires a free EGXAPI account and key.

---

## Architecture

```
┌─────────────────────────────────────────────┐
│             Scanner Pipeline                │
│  (Yahoo Finance — primary source)           │
└──────────────────┬──────────────────────────┘
                   │
         ┌─────────▼─────────┐
         │  Shadow Engine     │
         │  (shadow.py)       │
         └──┬─────────────┬──┘
            │             │
   ┌────────▼──┐    ┌─────▼───────┐
   │ Yahoo     │    │ EGXAPI      │
   │ fetch_    │    │ Provider    │
   │ live_     │    │ (read-only) │
   │ quote()   │    │             │
   └───────────┘    └─────────────┘
            │             │
         ┌──▼─────────────▼──┐
         │  _classify_status  │
         │  (per-symbol)      │
         └─────────┬──────────┘
                   │
         ┌─────────▼─────────┐
         │  CSV Log           │
         │  Summary Report    │
         └───────────────────┘
```

**Key Principle:** EGXAPI failures are always non-blocking. The scan continues regardless.

---

## Classification Statuses

| Status | Meaning | Threshold |
|--------|---------|-----------|
| `MATCH` | Prices within tolerance | ≤ 0.25% difference |
| `PRICE_DIFF` | Prices diverge | > 0.25% difference |
| `VOLUME_DIFF` | Prices match but volumes differ | Volume mismatch |
| `STALE_DATA` | EGXAPI timestamp too old | > 900 sec (15 min) |
| `NO_DATA` | One or both providers failed | Exception or None |

---

## Test Results

**Total: 38/38 PASS**

### Unit Tests (`test_shadow.py`) — 26 tests

| Category | Tests | Status |
|----------|-------|--------|
| ShadowStatus constants | 1 | PASS |
| SymbolComparison.to_csv_row | 1 | PASS |
| ShadowSummary.to_text / to_dict | 2 | PASS |
| _classify_status (match, price_diff, volume_diff, stale, no_data, edge cases) | 9 | PASS |
| CSV logging (path, create, append, columns) | 4 | PASS |
| run_shadow_comparison (match, price_diff, egxapi down, yahoo down, empty tickers) | 5 | PASS |
| Config defaults (DATA_PROVIDER, thresholds, env override) | 3 | PASS |

### Integration Tests (`test_shadow_integration.py`) — 12 tests

| Category | Tests | Status |
|----------|-------|--------|
| Full pipeline (all match, mixed, EGXAPI down, Yahoo down) | 4 | PASS |
| CSV column validation | 1 | PASS |
| Multiple appends to same file | 1 | PASS |
| Summary text output | 1 | PASS |
| Edge cases (empty list, price=0, None price, stale timestamp, volume from scan_results) | 5 | PASS |

---

## Files Implemented

| File | Purpose |
|------|---------|
| `providers/__init__.py` | Package init |
| `providers/egxapi_provider.py` | `EGXAPIProvider` class — read-only REST client |
| `providers/shadow.py` | Shadow comparison engine, CSV logging, summary |
| `providers/eval.py` | Standalone eval script (`python -m providers.eval`) |
| `tests/test_shadow.py` | 26 unit tests |
| `tests/test_shadow_integration.py` | 12 integration tests |
| `scanner/config.py` | `DATA_PROVIDER`, `SHADOW_PRICE_MATCH_THRESHOLD`, `SHADOW_STALE_THRESHOLD_SEC` |
| `main.py` | CLI shadow integration |
| `bot.py` | Telegram shadow integration + `/egxapi_status` |
| `.env.example` | Updated with `DATA_PROVIDER`, `EGXAPI_KEY`, `EGXAPI_ENV` |
| `requirements.txt` | Added `httpx>=0.25.0` |

---

## Configuration

```env
# .env
DATA_PROVIDER=shadow          # shadow | fallback | egxapi
EGXAPI_KEY=your_key_here      # Required for shadow/egxapi
EGXAPI_ENV=paper              # paper (default) or live
```

```python
# scanner/config.py defaults
DATA_PROVIDER = os.getenv("DATA_PROVIDER", "shadow")
SHADOW_PRICE_MATCH_THRESHOLD = 0.25   # percent
SHADOW_STALE_THRESHOLD_SEC = 900      # 15 minutes
```

---

## Setup Instructions

### 1. Get Free EGXAPI Key

1. Go to **https://egxapi.com/auth/#signup**
2. Create a free account
3. Copy your API key

### 2. Add to .env

```bash
EGXAPI_KEY=your_copied_key_here
EGXAPI_ENV=paper
DATA_PROVIDER=shadow
```

### 3. Run During Market Hours

EGX market hours: **Sun–Thu, 10:00 AM – 2:15 PM Cairo time**

```bash
# Full scan with shadow validation
python -m scanner.main

# Standalone shadow evaluation
python -m providers.eval

# Telegram bot (with shadow on every /scan)
python -m bot
```

### 4. Check Results

```bash
# CSV logs
dir logs\provider_validation_*.csv

# Latest summary printed after each scan
# Match rate % displayed in console and Telegram
```

---

## Non-Blocking Guarantees

| Scenario | Behavior |
|----------|----------|
| EGXAPI key missing | Returns `DATA_UNAVAILABLE`, scan continues |
| EGXAPI network error | Caught, logged, `NO_DATA`, scan continues |
| EGXAPI rate limit (429) | Retries 3x with backoff, then `NO_DATA` |
| EGXAPI server error (5xx) | Retries 3x with backoff, then `NO_DATA` |
| Yahoo Finance error | Caught, logged, `NO_DATA`, scan continues |
| Both providers fail | `NO_DATA` recorded, scan continues |
| CSV write failure | Logged as warning, scan continues |

---

## Blocking Safety

The `EGXAPIProvider` explicitly blocks all order-related methods:

```python
def create_order(...) -> NoReturn:   # raises NotImplementedError
def cancel_order(...) -> NoReturn:   # raises NotImplementedError
def get_orders(...) -> NoReturn:     # raises NotImplementedError
def get_positions(...) -> NoReturn:  # raises NotImplementedError
```

---

## Next Steps

1. **Get EGXAPI key** — Sign up at https://egxapi.com/auth/#signup
2. **Add key to .env** — Set `EGXAPI_KEY` in `D:\EGX Lite Bot\.env`
3. **Run during market hours** — Sun–Thu 10:00–14:15 Cairo time
4. **Monitor match rate** — Target: >90% MATCH after calibration
5. **Adjust thresholds** — Tune `SHADOW_PRICE_MATCH_THRESHOLD` if needed

---

## Known Limitations

- **EGXAPI API endpoints are assumed** — the actual API structure (`/v2/quotes/{symbol}`, `/v2/trades/{symbol}`, `/v2/bars/{symbol}`) needs verification with a real key
- **No live validation yet** — all tests use mocked providers
- **Telegram bot on Render Free Tier** — spins down after 15min; needs UptimeRobot keep-alive
- **EGXAPI package install** — timed out during `pip install egxapi`; httpx used directly instead

---

## Validation Checklist

- [x] Shadow comparison engine implemented
- [x] CSV logging with correct columns
- [x] Summary generation (text + dict)
- [x] Non-blocking error handling
- [x] Order method blocking
- [x] Thread pool parallel comparison
- [x] CLI integration (`main.py`)
- [x] Telegram integration (`bot.py`)
- [x] `/egxapi_status` command
- [x] 38/38 tests passing
- [ ] **EGXAPI key obtained** ← pending
- [ ] **Live validation during market hours** ← pending
- [ ] **Match rate >90% confirmed** ← pending
