# EGX Lite Market Radar v2.0 — Full Technical Audit Report

**Date:** 2026-07-24
**Branch:** `review/strategy-audit-v1`
**Scope:** Complete codebase — architecture, data layer, scanner logic, indicators, scoring, risk management, dashboard, telegram bot, security, production readiness
**Method:** Line-by-line review of all source files, tests, configuration, and deployment artifacts

---

## Executive Summary

EGX Lite Market Radar v2.0 is a **functional and well-architected** post-market activity scanner for the Egyptian Exchange. The system correctly detects unusual volume activity using a multi-source data pipeline (Yahoo Finance + TradingView + EGXAPI shadow), classifies activity into BUYING/SELLING/UNUSUAL categories, and formats results for Telegram and web dashboard consumption.

**Overall Assessment: 6.5/10** — Solid core logic with significant gaps in security, concurrency safety, and production hardening.

| Category | Score | Notes |
|---|---|---|
| Architecture | 8/10 | Clean separation of concerns, good module boundaries |
| Data Layer | 7/10 | Multi-source pipeline works well, but cache races and mutable returns |
| Scanner Logic | 8/10 | Activity scoring is transparent and well-tested |
| Indicators | 7/10 | Correct implementations, but several unused functions |
| Risk Management | 6/10 | Liquidity filters exist, but no circuit breakers or rate limiting |
| Telegram Bot | 7/10 | Good UX with bilingual support, but concurrency and error leak issues |
| Dashboard | 7/10 | Modern dark theme, good interactivity, but no auth |
| Security | 3/10 | Secrets exposed, no auth on any endpoint |
| Production Readiness | 4/10 | No WSGI server, no health checks, no monitoring |
| Test Coverage | 8/10 | 228 tests covering core logic well |

---

## 1. Architecture

### Strengths
- Clear module boundaries: `scanner/`, `providers/`, `bot.py`, `web_server.py`, `main.py`
- Configuration centralized in `config.py` with env-var overrides
- Data flow is linear and traceable: `config → data_provider → radar_data → market_radar → radar_output`
- Shadow provider design allows safe EGXAPI validation without production dependency
- ISM handoff uses neutral payload — no bias propagation from radar to decision layer

### Issues

| # | Severity | Issue | Location |
|---|---|---|---|
| A-1 | **HIGH** | **Circular dependency between `scanner/` and `providers/`** — `egxapi_provider.py:529` and `shadow.py:160` use lazy imports inside function bodies to avoid circular imports. This indicates an architectural boundary violation. | `providers/egxapi_provider.py`, `providers/shadow.py` |
| A-2 | **MEDIUM** | **Duplicated market hours logic** — `_is_market_hours()` in `data_provider.py:41` and `is_market_open()` in `radar_data.py:86` implement identical logic. Changes to one must be mirrored in the other. | `scanner/data_provider.py`, `scanner/radar_data.py` |
| A-3 | **MEDIUM** | **Duplicate config constants** — `MACD_FAST/SLOW/SIGNAL` (lines 72-74) and `RADAR_MACD_FAST/SLOW/SIGNAL` (lines 123-125) are separate but identical. Risk of drift. | `scanner/config.py` |
| A-4 | **LOW** | **String constants instead of enums** — `ActivityCategory`, `ActivityLevel`, `ShadowStatus` use plain strings. Python `enum.Enum` would provide type safety and IDE autocomplete. | `scanner/market_radar.py`, `providers/shadow.py` |
| A-5 | **LOW** | **No `__all__` in any module** — Public API boundaries are not explicitly defined, making it harder to understand module contracts. | All modules |

---

## 2. Data Layer

### Strengths
- Multi-source pipeline (Yahoo → TradingView overlay → EGXAPI shadow) is well-designed
- Cache separation between Yahoo and TradingView prevents contamination
- TV overlay validation rejects invalid data (scale mismatch, ORAS pattern, OHLC bounds)
- Freshness detection correctly handles trading days, market hours, and provider delays

### Issues

| # | Severity | Issue | Location |
|---|---|---|---|
| D-1 | **CRITICAL** | **Cache returns mutable references** — `_tv_batch_fetch()` returns `_TV_CACHE` directly (line 108). `_get_cached_yahoo_history()` returns `data` from cache (line 277). Callers that mutate the returned DataFrame will corrupt the cache. `fetch_history()` mitigates with `copy(deep=True)`, but `fetch_live_quote()` at line 369 does NOT copy. | `scanner/data_provider.py:108, 277` |
| D-2 | **HIGH** | **Race condition on `_TV_CACHE_TS`** — The global timestamp is read and written without locking. Two concurrent calls could both pass the TTL check and both fetch from the API. | `scanner/data_provider.py:59, 106` |
| D-3 | **HIGH** | **Race condition on `_CACHE` dict** — Same issue as D-2 for the Yahoo cache. No thread safety. | `scanner/data_provider.py:16` |
| D-4 | **MEDIUM** | **Division by zero risk** — `deviation = abs(price - prev_close) / prev_close` at line 421. If `prev_close` is 0, `ZeroDivisionError` occurs. The code checks `prev_close is not None` but not `prev_close != 0`. | `scanner/data_provider.py:421` |
| D-5 | **MEDIUM** | **Redundant retry logic** — `_get_cached_yahoo_history()` fetches once, waits 2s and fetches "1y", then fetches "1y" again immediately with no delay. The third attempt is identical to the second. | `scanner/data_provider.py:287-292` |
| D-6 | **MEDIUM** | **`_bars_are_valid` mutates input list** — `bars.clear()` and `bars.extend(filtered)` at lines 328-329 modify the caller's list. A "validation" function should not have side effects. | `scanner/radar_data.py:328-329` |
| D-7 | **LOW** | **`get_live_quote` ignores `fetch_live_quote`** — Despite `fetch_live_quote` in `data_provider.py` providing TradingView real-time data, `radar_data.get_live_quote()` reconstructs quotes from the daily DataFrame. Missed integration. | `scanner/radar_data.py:451` |
| D-8 | **LOW** | **Dead field `adjusted_close`** — `DailyBar.adjusted_close` is defined but never populated anywhere in the codebase. | `scanner/radar_data.py:198` |
| D-9 | **LOW** | **Dead constant `FAILURE_INVALID_OHLC`** — Defined at line 186 but never referenced. | `scanner/radar_data.py:186` |
| D-10 | **LOW** | **Unused `copy` import** — `copy` is imported at line 7 but never used (code uses `df.copy(deep=True)` instead). | `scanner/data_provider.py:7` |
| D-11 | **LOW** | **Inconsistent logging style** — Some `logger.debug()` calls use f-strings (lines 413, 426, 457), which evaluate even when debug is disabled. Others use `%s` lazy formatting. | `scanner/data_provider.py` |

---

## 3. Scanner Logic

### Strengths
- Activity scoring is transparent: 5 weighted components (Volume 50, Liquidity 15, Price-Volume 15, RSI 10, MACD 10)
- Multi-signal voting for category classification prevents single-indicator bias
- Parallel symbol analysis with `ThreadPoolExecutor(max_workers=8)`
- Liquidity filters exclude illiquid stocks from results
- RVOL correctly excludes latest session from its own average

### Issues

| # | Severity | Issue | Location |
|---|---|---|---|
| S-1 | **HIGH** | **Global config mutation** — `config.RADAR_MIN_AVG_TRADED_VALUE_20 = min_avg_value` at line 725 mutates module-level state. In concurrent calls, this creates a race condition. The mutation persists for the process lifetime, silently altering defaults for all future calls. | `scanner/market_radar.py:725` |
| S-2 | **MEDIUM** | **Import inside hot loop** — `import pandas as pd` at line 232 inside `_analyze_symbol()` is executed on every symbol call (34 times per scan). While Python caches imports, the repeated lookup is wasteful. | `scanner/market_radar.py:232` |
| S-3 | **MEDIUM** | **Volume percentile edge case** — `np.sum(vol_window <= latest_volume) / len(vol_window)` at line 224. If all volumes are equal, percentile is 100%, which is inflated. Standard percentile calculations would yield 50%. | `scanner/market_radar.py:224` |
| S-4 | **MEDIUM** | **Unused `force_refresh` parameter** — Accepted by `run_market_radar()` at line 701 but never used in the function body. | `scanner/market_radar.py:701` |
| S-5 | **LOW** | **Redundant traded value computation** — `traded_values = closes * volumes` at line 204, then `avg_traded_value_20_calc = float(np.mean(traded_values[-20:]))` at line 228. The variable `avg_traded_value_20` at line 205 uses the same slice, making them identical. | `scanner/market_radar.py:204-228` |
| S-6 | **LOW** | **Unused imports** — `get_live_quote`, `RadarQuote`, `FAILURE_INVALID_OHLC`, `FAILURE_INVALID_CLOSE` imported but never referenced. | `scanner/market_radar.py:29-31` |
| S-7 | **LOW** | **Naive timestamp** — `datetime.now()` at line 803 returns a timezone-naive datetime. Could cause issues if compared with timezone-aware datetimes. | `scanner/market_radar.py:803` |
| S-8 | **LOW** | **`_analyze_symbol` is 191 lines** — Very long for a single function. Could be decomposed into sub-functions for readability. | `scanner/market_radar.py:145-336` |

---

## 4. Indicators

### Strengths
- Pure pandas/numpy implementations avoid `pandas_ta`/`numba` compatibility issues
- RSI uses Wilder's smoothing matching TradingView conventions
- MACD implementation is standard and correct
- Each function is under 10 lines — minimal and focused

### Issues

| # | Severity | Issue | Location |
|---|---|---|---|
| I-1 | **MEDIUM** | **RSI division by zero** — If both `avg_gain` and `avg_loss` are 0 (flat price), `rs = 0/0 = NaN`. The caller handles this with `np.isnan()` check, but the indicator itself doesn't document this behavior. | `scanner/indicators.py:20` |
| I-2 | **LOW** | **Unused functions** — `atr()`, `ema_slope()`, `find_resistance()` are defined but never called by the radar pipeline. They may serve other modules or be future placeholders. | `scanner/indicators.py:33, 43, 48` |
| I-3 | **LOW** | **`ema` imported but unused** — `from scanner.indicators import ema` at `market_radar.py:30` but `ema()` is never called. | `scanner/market_radar.py:30` |
| I-4 | **LOW** | **No `__all__`** — Public API of the indicators module is not explicitly defined. | `scanner/indicators.py` |
| I-5 | **LOW** | **Missing docstrings** — `ema()`, `rsi()`, `macd()`, `atr()` lack docstrings. Only `ema_slope` and `find_resistance` have them. | `scanner/indicators.py` |

---

## 5. Scoring and Classification

### Strengths
- Score is always clamped to [0, 100]
- Each component is clamped to its max weight
- Category classification uses multi-signal voting (not single indicator)
- Minimum signal threshold (3 signals + 0.6 ratio) prevents false positives

### Issues

| # | Severity | Issue | Location |
|---|---|---|---|
| SC-1 | **MEDIUM** | **`decisions.py` is disconnected** — Its `classify()` function uses `config.DECISION_READY/WATCH/MONITOR` thresholds (90/80/70) that appear designed for ISM scoring, not radar activity scoring. The two systems use the same 0-100 range but different components, creating confusion. | `scanner/decisions.py` |
| SC-2 | **LOW** | **`DECISION_EMOJI` unused** — Defined but never referenced within the module or imported by other modules. | `scanner/decisions.py:7-12` |
| SC-3 | **LOW** | **`format_short_reason` ignores `category`** — The parameter is accepted but never used in the function body. | `scanner/radar_output.py:131` |

---

## 6. Risk Management

### Strengths
- Liquidity filter excludes stocks below `MIN_VALUE_TRADED` (5M EGP)
- Price filter excludes stocks below `RADAR_MIN_PRICE` (1.0 EGP)
- Minimum history requirement (60 candles) prevents analysis of thin data
- TradingView overlay validation rejects manipulated data (ORAS pattern)
- Shadow comparison provides independent validation

### Issues

| # | Severity | Issue | Location |
|---|---|---|---|
| R-1 | **HIGH** | **No rate limiting on bot commands** — A user spamming buttons could trigger many concurrent scans. `_radar_lock` serializes scans but doesn't limit them. | `bot.py` |
| R-2 | **HIGH** | **No rate limiting on web API** — `/api/radar/refresh` (POST) has no auth or rate limit. An attacker could spam this to cause repeated expensive scans (DoS). | `web_server.py:184` |
| R-3 | **MEDIUM** | **No circuit breaker for data providers** — If Yahoo Finance is down, every symbol analysis will retry and sleep. No global timeout or fallback to cached data. | `scanner/data_provider.py` |
| R-4 | **MEDIUM** | **Exception messages leaked to users** — Raw exception messages sent directly to Telegram users (e.g., `f"Error: {e}"`). Could leak internal paths or stack details. | `bot.py:279, 349, 371, 557, 663` |
| R-5 | **LOW** | **Shadow comparison `provider_bid/ask` type bug** — `provider_bid: Optional[None]` and `provider_ask: Optional[None]` should be `Optional[float]`. These fields are never assigned. | `providers/shadow.py:71-74` |

---

## 7. Telegram Bot

### Strengths
- Clean `MsgContext` abstraction unifying callback and message flows
- Bilingual Arabic/English UI
- Card-style v2 formatter with emoji progress bars
- Async lock prevents concurrent scan corruption
- TTL cache prevents redundant scans within 5 minutes

### Issues

| # | Severity | Issue | Location |
|---|---|---|---|
| T-1 | **HIGH** | **Undefined name `format_radar_header`** — `handle_radar_category()` at line 382 calls `format_radar_header(result)`, but this function is NOT imported at the top of the file. Will cause `NameError` at runtime. | `bot.py:382` |
| T-2 | **HIGH** | **Race condition on `_last_scan`** — The legacy scan cache has NO locking or TTL. Multiple concurrent users could overwrite it simultaneously. | `bot.py:155-161` |
| T-3 | **HIGH** | **`_last_radar` read outside lock** — `handle_radar_category` reads `_last_radar["result"]` at line 359 OUTSIDE the lock, before potentially entering the lock at line 363. | `bot.py:358-363` |
| T-4 | **MEDIUM** | **Exception messages leak to users** — Raw exception messages sent to Telegram users. | `bot.py:279, 349, 371, 557, 663` |
| T-5 | **MEDIUM** | **`send_to_ism_command` no null check** — Accesses `_last_radar["result"].all_items` without checking if result is None or has the attribute. | `bot.py:297` |
| T-6 | **LOW** | **Dead code `_run_radar_sync`** — Defined at line 189 but never called. | `bot.py:189-193` |
| T-7 | **LOW** | **f-strings in logger calls** — Defeats lazy evaluation. Lines 278, 311, 348, 370, 556, 661. | `bot.py` |
| T-8 | **LOW** | **`split_radar_messages` imported inside function** — Line 387 imports inside function body rather than at module level. Intentional to avoid circular imports but hurts readability. | `bot.py:387` |
| T-9 | **LOW** | **`os.system("chcp 65001")`** — Shell execution anti-pattern. `subprocess.run()` would be safer. | `bot.py:16` |

---

## 8. Web Server and Dashboard

### Strengths
- Dark theme with bilingual (AR/EN) support
- 34-stock table with search/filter/sort
- Stock detail slide-out panel
- CSV export functionality
- Auto-refresh with configurable interval
- Scan history tracking

### Issues

| # | Severity | Issue | Location |
|---|---|---|---|
| W-1 | **HIGH** | **Race condition in `api_radar()`** — Cache check is under `_lock`, but the actual scan at line 173 runs OUTSIDE the lock. Two concurrent requests could both see stale cache and both run expensive scans. | `web_server.py:163-181` |
| W-2 | **HIGH** | **Unauthenticated POST endpoint** — `/api/radar/refresh` accepts POST with no auth. DoS vector. | `web_server.py:184` |
| W-3 | **MEDIUM** | **No error handling on radar API** — If `_run_scan_fresh()` throws, returns 500 with no user-friendly message. | `web_server.py:163-196` |
| W-4 | **MEDIUM** | **JSON history file not atomic** — If server crashes mid-write, `scan_history.json` could be corrupted. Write-to-temp-then-rename would be safer. | `web_server.py:135-136` |
| W-5 | **LOW** | **Dead code `_run_scan()`** — Defined at line 103 but never called. | `web_server.py:103-105` |
| W-6 | **LOW** | **No CORS headers** — If dashboard or API is consumed cross-origin, CORS issues will arise. | `web_server.py` |
| W-7 | **LOW** | **No API documentation** — No Swagger/OpenAPI spec for the REST API. | `web_server.py` |

---

## 9. Security

### Critical Findings

| # | Severity | Issue | Location |
|---|---|---|---|
| SEC-1 | **CRITICAL** | **Live Telegram bot token exposed in plaintext** — `8888708402:AAGRSpdd5zt_mXqb5p2P4MT_ZUOFstIEgOo` in `.env`. Anyone with this token can control the bot. If committed to a public repo, this is a full compromise. | `.env:1` |
| SEC-2 | **CRITICAL** | **Live EGXAPI key exposed in plaintext** — `egx_live_2wokHYMVqTHo6vHFtEcmYqTd` in `.env`. Despite `EGXAPI_ENV=paper`, the key prefix suggests production credential. | `.env:2` |
| SEC-3 | **HIGH** | **No authentication on any endpoint** — `/api/radar`, `/api/radar/refresh`, `/api/history`, `/dashboard` are all publicly accessible. | `web_server.py` |
| SEC-4 | **HIGH** | **No Telegram bot allowlist** — Any user who finds the bot can use all commands. No admin check. | `bot.py` |
| SEC-5 | **MEDIUM** | **Exception messages leak to users** — Could expose internal paths, stack details, or database info. | `bot.py:279, 349, 371, 557, 663` |
| SEC-6 | **LOW** | **Shell execution** — `os.system("chcp 65001 >nul 2>&1")` on Windows. Hardcoded, not user-controlled, but `os.system` is generally discouraged. | `bot.py:16`, `main.py:21` |

### Recommendations
1. **Rotate the Telegram bot token immediately** via BotFather
2. **Rotate the EGXAPI key** if it's a production credential
3. Add `.env` to `.gitignore` (verify it's not tracked)
4. Add Telegram user allowlist for bot commands
5. Add API key authentication for web API endpoints
6. Sanitize exception messages before sending to users

---

## 10. Production Readiness

### Issues

| # | Severity | Issue | Location |
|---|---|---|---|
| P-1 | **HIGH** | **No production WSGI server** — Flask's built-in development server is used. `gunicorn` (Linux) or `waitress` (Windows) should be used. | `requirements.txt`, `web_server.py:214` |
| P-2 | **HIGH** | **No pinned dependency versions** — All deps use `>=` with no upper bounds. A breaking change in any dependency could crash the service. | `requirements.txt` |
| P-3 | **MEDIUM** | **No health check beyond `/health`** — No monitoring, alerting, or metrics. | `web_server.py:147` |
| P-4 | **MEDIUM** | **No structured logging** — Logs are plain text. JSON structured logging would improve observability. | All modules |
| P-5 | **MEDIUM** | **Flask + Telegram in same process** — A Flask crash won't kill the bot (daemon thread), but a bot crash WILL kill Flask. | `web_server.py:217-218` |
| P-6 | **MEDIUM** | **No graceful shutdown** — No signal handling for SIGTERM/SIGINT. | `web_server.py`, `bot.py` |
| P-7 | **LOW** | **`yfinance` version floor too low** — `>=0.2.0` is extremely old. Should be `>=0.2.31`. | `requirements.txt` |
| P-8 | **LOW** | **No `--version` flag** — CLI lacks a version flag. | `main.py` |
| P-9 | **LOW** | **No dev dependencies** — No test framework, linter, formatter, or type checker in requirements. | `requirements.txt` |

---

## 11. Test Coverage

### Strengths
- 228 tests across 3 test files
- Excellent regression coverage for OHLC mapping bug (8 dedicated tests)
- Session date detection thoroughly tested (10 combinations)
- Data freshness assessment tested with production regression case
- v1→v2 formatter migration verified with source code introspection
- Activity score boundary testing (sweep of RVOL 0.1-5.0, percentile 5-95)

### Gaps

| # | Gap | Impact |
|---|---|---|
| T-1 | **No async tests** — All tests are synchronous. The asyncio lock behavior in `bot.py` is not tested. | Concurrency bugs in production |
| T-2 | **No integration tests for web API** — `/api/radar`, `/api/radar/refresh` are not tested. | API bugs undetected |
| T-3 | **No security tests** — No tests for auth, rate limiting, or input validation. | Security regressions |
| T-4 | **No performance tests** — No load testing or benchmarking. | Performance regressions |
| T-5 | **No tests for `shadow.py`** — The shadow comparison logic is not unit-tested. | Shadow bugs undetected |
| T-6 | **No tests for `main.py` CLI** — The Typer CLI commands are not tested. | CLI regressions |
| T-7 | **Source code introspection test is brittle** — `test_all_handlers_use_v2` uses string matching on `bot.py` source. If the function name appears in a comment, it false-positives. | False test failures |

---

## 12. Code Quality

### Cross-Cutting Issues

| # | Issue | Files Affected |
|---|---|---|
| Q-1 | **Dead code** — `_run_radar_sync` (bot.py:189), `_run_scan` (web_server.py:103), `FAILURE_INVALID_OHLC` (radar_data.py:186), `adjusted_close` (radar_data.py:198), `egxapi_quote_volume` (shadow.py:178), `DECISION_EMOJI` (decisions.py:7), unused indicator functions | Multiple |
| Q-2 | **Duplicate code** — Market hours logic (2 files), Telegram formatters v1/v2 (radar_output.py), helper functions (egxapi_provider.py), MACD config (config.py) | Multiple |
| Q-3 | **Inconsistent logging** — Mix of f-strings and `%s` formatting in logger calls | bot.py, data_provider.py |
| Q-4 | **No type hints on many functions** — `_compare_one_symbol`, `_result_to_dict`, `create_handoff` parameter | shadow.py, web_server.py, ism_handoff.py |
| Q-5 | **Large functions** — `_analyze_symbol` (191 lines), `_classify_category` (70 lines), `format_radar_telegram` (110 lines) | market_radar.py, radar_output.py |
| Q-6 | **RadarOutput duplication** — v1 and v2 Telegram formatters duplicate ~150 lines. v1 should be deprecated and removed. | scanner/radar_output.py |

---

## Prioritized Action Plan

### Immediate (This Week)
1. **Rotate secrets** — Telegram bot token + EGXAPI key
2. **Fix `format_radar_header` import** in `bot.py` — Runtime crash bug (T-1)
3. **Add `.env` to `.gitignore`** if not already tracked
4. **Add `gunicorn`/`waitress`** to requirements.txt

### Short-Term (Next 2 Weeks)
5. **Fix cache mutable returns** — Return copies from `_tv_batch_fetch()` and `_get_cached_yahoo_history()` (D-1)
6. **Add locks to data caches** — Thread-safe access to `_CACHE`, `_TV_CACHE`, `_TV_CACHE_TS` (D-2, D-3)
7. **Fix global config mutation** in `run_market_radar()` (S-1)
8. **Add API authentication** to web server endpoints (SEC-3)
9. **Add Telegram user allowlist** (SEC-4)
10. **Pin dependency versions** in requirements.txt (P-2)

### Medium-Term (Next Month)
11. **Consolidate market hours logic** into a single function (A-2)
12. **Remove v1 Telegram formatter** and dead code (Q-1, Q-6)
13. **Add async tests** for bot concurrency (T-1)
14. **Add web API integration tests** (T-2)
15. **Implement rate limiting** on bot and web API (R-1, R-2)
16. **Sanitize exception messages** before sending to users (R-4)
17. **Add structured logging** (P-4)

### Long-Term (Next Quarter)
18. **Refactor `RadarItem`** into compositional sub-objects (OHLC, RSI, MACD, etc.)
19. **Add circuit breaker** for data providers
20. **Add monitoring and alerting** (Prometheus metrics, health checks)
21. **Migrate to enum.Enum** for all string constants
22. **Add type hints** across all modules
23. **Add `--output-format json`** to CLI

---

## Appendix: File Inventory

| File | Lines | Status |
|---|---|---|
| `scanner/config.py` | 182 | Clean — minor duplication concern |
| `scanner/data_provider.py` | 539 | Critical cache safety issues |
| `scanner/radar_data.py` | 519 | Good — minor mutation and dead code |
| `scanner/market_radar.py` | 826 | Good core logic — config mutation bug |
| `scanner/indicators.py` | 60 | Clean — unused functions |
| `scanner/radar_output.py` | 618 | Good — v1/v2 duplication |
| `scanner/decisions.py` | 23 | Minimal — disconnected from radar |
| `scanner/ism_handoff.py` | 82 | Clean — good design |
| `providers/egxapi_provider.py` | 701 | Good — duplicate helpers |
| `providers/shadow.py` | 357 | Good — type bugs on bid/ask |
| `bot.py` | 968 | Critical import bug + race conditions |
| `web_server.py` | 224 | Critical race condition + no auth |
| `main.py` | 152 | Clean — no error handling |
| `tests/test_market_radar.py` | 1020 | Excellent coverage |
| `tests/test_telegram_formatter.py` | 668 | Excellent v2 migration tests |
| `tests/test_data_refresh.py` | 609 | Good cache/overlay tests |
| `dashboard/index.html` | — | Modern dark theme |
| `dashboard/style.css` | — | Clean |
| `dashboard/app.js` | — | Good interactivity |
| `.env` | 4 | **CRITICAL: Secrets exposed** |
| `requirements.txt` | 10 | No pinned versions |

---

*Report generated by opencode on 2026-07-24*
*Branch: review/strategy-audit-v1*
