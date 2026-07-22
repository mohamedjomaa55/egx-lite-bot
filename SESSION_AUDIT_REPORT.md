# EGX Lite Market Radar — Session Audit Report

**Date**: 2026-07-22 10:38 Cairo  
**Auditor**: `python -m scanner.audit_radar_session`  
**Tests**: 112/112 passing

---

## 1. Detection Count: Why Only 1-3 Stocks?

### Production output (before TradingView): 1 stock (LCSW)
### Audit output (with TradingView): 3 stocks (ORAS, FAITA, LCSW)

**Root cause: 31/34 stocks have `activity_level = NORMAL`.**

The ELEVATED threshold requires **either**:
- RVOL >= 1.35, **or**
- Volume percentile >= 75

At 10:38 AM Cairo (only ~1 hour into the session), most stocks have very low partial-day volume compared to their 20-day full-session average. This produces RVOL values well below 1.0x for 30/34 stocks.

| Metric | Value |
|--------|-------|
| Stocks at EXTREME | 1 (ORAS — data anomaly) |
| Stocks at HIGH | 1 (FAITA — excluded by liquidity) |
| Stocks at ELEVATED | 1 (LCSW — legitimate) |
| Stocks at NORMAL | 31 |
| Avg score (all) | 23.3 |
| Avg score (excluded) | 20.0 |

### Stocks matching 2+ activity conditions but excluded

| Ticker | Conditions | Score | Level | Why excluded |
|--------|-----------|-------|-------|-------------|
| ETEL | move=+6.2%, range_pos=90% | 34 | NORMAL | RVOL=0.65x below ELEVATED |
| ACGC | move=+6.4%, range_pos=97% | 30 | NORMAL | RVOL=0.89x below ELEVATED |
| ADIB | move=+5.2%, range_pos=6% | 25 | NORMAL | RVOL=0.20x below ELEVATED |
| ISPH | move=+2.9%, range_pos=94% | 25 | NORMAL | RVOL=0.47x below ELEVATED |
| RACC | move=-2.2%, range_pos=12% | 20 | NORMAL | RVOL=0.09x below ELEVATED |

**Key insight**: These stocks have significant price moves (2-6%) but insufficient volume to trigger the ELEVATED threshold. The radar is volume-first by design — price moves without volume are not flagged.

---

## 2. Session Metadata Audit

### Inputs traced:
```
Cairo current datetime   : 2026-07-22 10:38:13 EEST
Day of week              : Wednesday
Is trading day           : True
Market open (is_market_open): True
Session window           : 9:30 - 14:15 Cairo
Close + buffer           : 14:15 + 30min = 14:45
```

### Branch trace:
```
now_cairo < session_complete_time (10:38 < 14:45): True
  → Branch: before session complete → check_date = yesterday (2026-07-21)
  → 2026-07-21 is Tuesday (trading day) → expected = 2026-07-21

assess_data_freshness("2026-07-22") with is_market_open=True:
  → Branch 1: market open → return MARKET_OPEN
```

### Result:
```
provider_latest_date     = 2026-07-22  (TradingView live data — today's partial session)
expected_latest_session  = 2026-07-21  (last COMPLETED session — yesterday)
freshness_status         = MARKET_OPEN
delay_days               = -1          (provider has future data)
```

### Verdict: **VALID**

| Field | Value | Explanation |
|-------|-------|-------------|
| `provider_latest_date` | 2026-07-22 | TradingView provides live partial-session data for today |
| `expected_latest_session` | 2026-07-21 | During market hours, the latest *completed* session is yesterday |
| `freshness_status` | MARKET_OPEN | Correct — market is currently in session |
| `delay_days` | -1 | Provider has data from a future date (today) relative to expected (yesterday) — this is expected during MARKET_OPEN |

The "inconsistency" (provider=2026-07-22 vs expected=2026-07-21) is **by design**: during market hours, the provider has live data from today's partial session, while the expected completed session is yesterday.

---

## 3. ORAS Data Anomaly

**ORAS shows +905.6% change (71.05 → 714.50) — FALSE SIGNAL**

| Bar | Close | Volume | Source |
|-----|-------|--------|--------|
| 2026-07-20 | 71.05 | 0 | Yahoo Finance (corrupted) |
| 2026-07-22 | 714.50 | 30,432 | TradingView (live) |

**Root cause**: Yahoo Finance has corrupted data for ORAS — all recent bars show `Close=71.05, Volume=0`. TradingView's live bar (`714.50`) is appended as today's session, creating a fake `+905.6%` change. This triggers EXTREME activity detection.

**This is a TradingView overlay regression**: Without TradingView, ORAS would be excluded by the liquidity filter (0 volume → 0 traded value). With TradingView, the live bar passes the liquidity check and the fake price change triggers EXTREME.

**Only ORAS is affected** — it's the only stock with zero-volume bars in Yahoo Finance.

**Recommendation**: Add a data quality guard in `fetch_history()` to reject TradingView overlays when the Yahoo history shows zero volume across multiple recent bars (indicates corrupted/stale data).

---

## 4. Full 34-Stock Diagnostic Table

| Ticker | Provider | Close | Chg% | RVOL | TV_Ratio | CLV | RSI | MACD_H | Score | Level | Category | Included | Exclusion |
|--------|----------|-------|------|------|----------|-----|-----|--------|-------|-------|----------|----------|-----------|
| ORAS | 2026-07-22 | 714.50 | +905.6% | 0.00x | 20.00x | 0.83 | 100.0 | +41.0635 | 68 | EXTREME | BUYING | YES | data anomaly |
| FAITA | 2026-07-22 | 0.98 | -0.3% | 2.57x | 2.26x | 0.00 | 42.7 | -0.0008 | 64 | HIGH | SELLING | NO | liquidity (avg=39K < 1M) |
| LCSW | 2026-07-22 | 33.83 | -3.3% | 0.87x | 0.93x | 0.22 | 68.6 | +0.3553 | 40 | ELEVATED | SELLING | YES | — |
| ETEL | 2026-07-22 | 105.99 | +6.2% | 0.65x | 0.69x | 0.90 | 75.1 | +0.8234 | 34 | NORMAL | BUYING | no | level=NORMAL |
| ACGC | 2026-07-22 | 10.41 | +6.4% | 0.89x | 0.92x | 0.97 | 65.6 | +0.0583 | 30 | NORMAL | BUYING | no | level=NORMAL |
| MCQE | 2026-07-22 | 190.01 | -1.0% | 0.12x | 0.13x | 0.01 | 64.9 | +2.1254 | 29 | NORMAL | SELLING | no | level=NORMAL |
| OLFI | 2026-07-22 | 23.58 | +3.4% | 0.95x | 0.94x | 0.69 | 67.8 | +0.0342 | 28 | NORMAL | BUYING | no | level=NORMAL |
| ADIB | 2026-07-22 | 49.43 | +5.2% | 0.20x | 0.21x | 0.06 | 61.7 | +0.1612 | 25 | NORMAL | BUYING | no | level=NORMAL |
| ISPH | 2026-07-22 | 11.80 | +2.9% | 0.47x | 0.47x | 0.94 | 53.8 | +0.0040 | 25 | NORMAL | BUYING | no | level=NORMAL |
| PHDC | 2026-07-22 | 15.28 | +4.3% | 0.33x | 0.33x | 0.72 | 58.2 | -0.0417 | 25 | NORMAL | BUYING | no | level=NORMAL |
| AMOC | 2026-07-22 | 8.32 | +0.5% | 0.09x | 0.10x | 0.89 | 62.8 | +0.0776 | 24 | NORMAL | BUYING | no | level=NORMAL |
| EGAL | 2026-07-22 | 303.49 | -0.2% | 0.07x | 0.07x | 0.83 | 53.9 | +2.3825 | 24 | NORMAL | UNUSUAL | no | level=NORMAL |
| MASR | 2026-07-22 | 8.29 | -0.7% | 0.09x | 0.09x | 0.22 | 68.1 | +0.0494 | 24 | NORMAL | SELLING | no | level=NORMAL |
| OCDI | 2026-07-22 | 27.56 | -0.1% | 0.00x | 0.00x | 0.87 | 65.9 | +0.0210 | 23 | NORMAL | UNUSUAL | no | level=NORMAL |
| EGAS | 2026-07-22 | 52.85 | +0.5% | 0.08x | 0.08x | 0.77 | 61.2 | +0.1790 | 21 | NORMAL | BUYING | no | level=NORMAL |
| IFAP | 2026-07-22 | 19.12 | +0.6% | 0.13x | 0.12x | 0.05 | 42.3 | -0.0205 | 21 | NORMAL | UNUSUAL | no | level=NORMAL |
| ORWE | 2026-07-22 | 23.10 | -0.4% | 0.12x | 0.12x | 0.83 | 54.4 | +0.0446 | 21 | NORMAL | UNUSUAL | no | level=NORMAL |
| RMDA | 2026-07-22 | 5.02 | +1.0% | 0.12x | 0.12x | 0.75 | 52.7 | -0.0032 | 21 | NORMAL | BUYING | no | level=NORMAL |
| EFIH | 2026-07-22 | 23.18 | +4.7% | 0.09x | 0.09x | 0.40 | 63.3 | +0.0820 | 20 | NORMAL | BUYING | no | level=NORMAL |
| RACC | 2026-07-22 | 9.97 | -2.2% | 0.09x | 0.09x | 0.12 | 49.4 | +0.0022 | 20 | NORMAL | SELLING | no | level=NORMAL |
| JUFO | 2026-07-22 | 29.09 | +1.0% | 0.28x | 0.27x | 0.66 | 42.2 | -0.2449 | 19 | NORMAL | BUYING | no | level=NORMAL |
| SAUD | 2026-07-22 | 22.06 | +2.2% | 0.22x | 0.23x | 0.52 | 56.6 | +0.0944 | 18 | NORMAL | UNUSUAL | no | level=NORMAL |
| EFID | 2026-07-22 | 28.13 | +2.1% | 0.07x | 0.07x | 0.72 | 51.7 | -0.0049 | 17 | NORMAL | BUYING | no | level=NORMAL |
| ATQA | 2026-07-22 | 9.68 | +0.7% | 0.16x | 0.16x | 0.75 | 54.1 | +0.0194 | 16 | NORMAL | BUYING | no | level=NORMAL |
| FAIT | 2026-07-22 | 37.67 | +1.1% | 0.15x | 0.15x | 0.06 | 59.9 | +0.0791 | 16 | NORMAL | UNUSUAL | no | level=NORMAL |
| MPCO | 2026-07-22 | 1.85 | -0.5% | 0.03x | 0.03x | 0.00 | 52.4 | -0.0084 | 16 | NORMAL | SELLING | no | level=NORMAL |
| SKPC | 2026-07-22 | 16.03 | +0.2% | 0.11x | 0.12x | 0.61 | 47.3 | +0.0228 | 16 | NORMAL | UNUSUAL | no | level=NORMAL |
| ARCC | 2026-07-22 | 56.80 | -0.2% | 0.20x | 0.20x | 0.29 | 57.0 | +0.1293 | 15 | NORMAL | UNUSUAL | no | level=NORMAL |
| ICFC | 2026-07-22 | 15.48 | +1.3% | 0.08x | 0.08x | 0.24 | 56.5 | +0.0798 | 15 | NORMAL | UNUSUAL | no | level=NORMAL |
| TMGH | 2026-07-22 | 101.55 | -0.3% | 0.04x | 0.04x | 0.52 | 63.1 | +0.4854 | 14 | NORMAL | UNUSUAL | no | level=NORMAL |
| ORHD | 2026-07-22 | 39.48 | +2.8% | 0.11x | 0.11x | 0.43 | 58.9 | -0.1404 | 13 | NORMAL | UNUSUAL | no | level=NORMAL |
| CIRA | 2026-07-22 | 31.90 | +1.2% | 0.14x | 0.14x | 0.46 | 67.3 | +0.1007 | 11 | NORMAL | UNUSUAL | no | level=NORMAL |
| ETRS | 2026-07-22 | 10.84 | -0.0% | 0.01x | 0.01x | 0.33 | 58.7 | -0.0926 | 10 | NORMAL | UNUSUAL | no | level=NORMAL |
| MTIE | 2026-07-22 | 9.45 | +0.1% | 0.04x | 0.04x | 0.56 | 55.2 | -0.0029 | 8 | NORMAL | UNUSUAL | no | level=NORMAL |

---

## 5. Recommendation

| Finding | Category | Recommendation |
|---------|----------|----------------|
| Low detection count (1-3/34) | **Threshold design issue** | By design — volume-first approach means most stocks at NORMAL during partial sessions. Consider running radar only after 12:00 PM for meaningful RVOL. |
| Session metadata (provider=07-22, expected=07-21) | **No change needed** | Valid — MARKET_OPEN status correctly reflects live partial session vs completed session. |
| ORAS false EXTREME signal | **Data quality bug** | Add zero-volume detection in `fetch_history()` to reject TradingView overlay when Yahoo history has corrupted data. |
| FAITA excluded despite HIGH level | **No change needed** | Correctly excluded by liquidity filter (avg traded value 39K < 1M threshold). |

### Files modified (diagnostic only):
- `scanner/audit_radar_session.py` — new audit script
- `data/audit_results.json` — full diagnostic output

### Tests: 112/112 passing (no regressions)
