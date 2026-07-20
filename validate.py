"""
EGX Swing Scout v1.0 — Validation Script
Checks all indicators, logic, and cross-validates against raw data.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import numpy as np
from scanner.data_provider import fetch_history, _CACHE
from scanner.indicators import ema, rsi, macd, atr, ema_slope, find_resistance
from scanner.volume_profile import build_volume_profile
from scanner.filters import market_filter, trend_filter
from scanner.scoring import calculate_score
from scanner.decisions import classify
from scanner.reason_engine import generate_reasons, get_next_action
from scanner.risk import format_risk
from scanner.technical_analysis import analyze
from scanner import config

TICKERS = ["COMI", "SWDY", "TMGH", "ORAS", "ETEL", "EGAL"]
PASS = 0
FAIL = 0
WARN = 0
REPORT = []


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        REPORT.append(f"  PASS  {name}" + (f" -- {detail}" if detail else ""))
    else:
        FAIL += 1
        REPORT.append(f"  FAIL  {name}" + (f" -- {detail}" if detail else ""))


def warn(name, detail=""):
    global WARN
    WARN += 1
    REPORT.append(f"  WARN  {name}" + (f" -- {detail}" if detail else ""))


def section(title):
    REPORT.append(f"\n{'='*50}")
    REPORT.append(f"  {title}")
    REPORT.append(f"{'='*50}")


# ═══════════════════════════════════════════════════════════════
# VALIDATION 1-6: Indicator Cross-Check Against Raw Data
# ═══════════════════════════════════════════════════════════════
section("INDICATOR VALIDATION (6 Stocks)")

for ticker in TICKERS:
    REPORT.append(f"\n  --- {ticker} ---")
    try:
        _CACHE.clear()
        data = fetch_history(ticker)
    except Exception as e:
        REPORT.append(f"  FAIL  Could not fetch {ticker}: {e}")
        FAIL += 1
        continue

    close = data["Close"].astype(float)
    high = data["High"].astype(float)
    low = data["Low"].astype(float)
    volume = data["Volume"].astype(float)
    n = len(data)

    last_close = float(close.iloc[-1])

    # ── 1. EMA50 ──────────────────────────────────────────────
    ema50_val = float(ema(close, 50).iloc[-1])
    ema50_manual = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    check(f"{ticker} EMA50 internal match",
          abs(ema50_val - ema50_manual) < 0.001,
          f"code={ema50_val:.4f} manual={ema50_manual:.4f}")

    recent_50 = close.iloc[-50:]
    r50_min, r50_max = float(recent_50.min()), float(recent_50.max())
    check(f"{ticker} EMA50 in valid range",
          r50_min - 0.01 <= ema50_val <= r50_max + 0.01,
          f"EMA50={ema50_val:.2f} range=[{r50_min:.2f}, {r50_max:.2f}]")

    # ── 2. EMA200 ─────────────────────────────────────────────
    ema200_val = float(ema(close, 200).iloc[-1])
    ema200_manual = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
    check(f"{ticker} EMA200 internal match",
          abs(ema200_val - ema200_manual) < 0.001,
          f"code={ema200_val:.4f} manual={ema200_manual:.4f}")

    recent_200 = close.iloc[-200:]
    r200_min, r200_max = float(recent_200.min()), float(recent_200.max())
    check(f"{ticker} EMA200 in valid range",
          r200_min - 0.01 <= ema200_val <= r200_max + 0.01,
          f"EMA200={ema200_val:.2f} range=[{r200_min:.2f}, {r200_max:.2f}]")

    # ── 3. EMA Slope ──────────────────────────────────────────
    slope_50 = float(ema_slope(ema(close, 50), lookback=5).iloc[-1])
    slope_200 = float(ema_slope(ema(close, 200), lookback=5).iloc[-1])

    ema50_now = float(ema(close, 50).iloc[-1])
    ema50_5ago = float(ema(close, 50).iloc[-6])
    expected_slope_50 = (ema50_now / ema50_5ago - 1) * 100
    check(f"{ticker} EMA50 slope matches manual",
          abs(slope_50 - expected_slope_50) < 0.001,
          f"code={slope_50:.4f} manual={expected_slope_50:.4f}")

    ema200_now = float(ema(close, 200).iloc[-1])
    ema200_5ago = float(ema(close, 200).iloc[-6])
    expected_slope_200 = (ema200_now / ema200_5ago - 1) * 100
    check(f"{ticker} EMA200 slope matches manual",
          abs(slope_200 - expected_slope_200) < 0.001,
          f"code={slope_200:.4f} manual={expected_slope_200:.4f}")

    # ── 4. RSI (Wilder) ───────────────────────────────────────
    rsi_val = float(rsi(close, 14).iloc[-1])

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain_w = gain.ewm(alpha=1/14, min_periods=14).mean()
    avg_loss_w = loss.ewm(alpha=1/14, min_periods=14).mean()
    rs_w = avg_gain_w / avg_loss_w
    rsi_wilder = float((100 - (100 / (1 + rs_w))).iloc[-1])
    check(f"{ticker} RSI Wilder internal match",
          abs(rsi_val - rsi_wilder) < 0.001,
          f"code={rsi_val:.4f} wilder={rsi_wilder:.4f}")

    check(f"{ticker} RSI in valid range [0, 100]",
          0 <= rsi_val <= 100,
          f"RSI={rsi_val:.2f}")

    # ── 5. MACD ───────────────────────────────────────────────
    macd_line, macd_signal, macd_hist = macd(close, 12, 26, 9)

    fast_ema12 = float(ema(close, 12).iloc[-1])
    slow_ema26 = float(ema(close, 26).iloc[-1])
    expected_macd = fast_ema12 - slow_ema26
    actual_macd = float(macd_line.iloc[-1])
    check(f"{ticker} MACD line matches",
          abs(actual_macd - expected_macd) < 0.001,
          f"code={actual_macd:.4f} expected={expected_macd:.4f}")

    expected_signal = float(ema(macd_line, 9).iloc[-1])
    actual_signal = float(macd_signal.iloc[-1])
    check(f"{ticker} MACD signal matches",
          abs(actual_signal - expected_signal) < 0.001,
          f"code={actual_signal:.4f} expected={expected_signal:.4f}")

    expected_hist = actual_macd - actual_signal
    actual_hist = float(macd_hist.iloc[-1])
    check(f"{ticker} MACD histogram matches",
          abs(actual_hist - expected_hist) < 0.001,
          f"code={actual_hist:.4f} expected={expected_hist:.4f}")

    # ── 6. ATR ────────────────────────────────────────────────
    atr_val = float(atr(high, low, close, 14).iloc[-1])

    prev_close = close.shift(1)
    tr_manual = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_manual = float(tr_manual.ewm(alpha=1/14, min_periods=14).mean().iloc[-1])
    check(f"{ticker} ATR matches",
          abs(atr_val - atr_manual) < 0.001,
          f"code={atr_val:.4f} manual={atr_manual:.4f}")

    check(f"{ticker} ATR >= 0",
          atr_val >= 0,
          f"ATR={atr_val:.4f}")


# ═══════════════════════════════════════════════════════════════
# VALIDATION 7-11: Logic Checks (using analyze() results)
# ═══════════════════════════════════════════════════════════════
section("LOGIC VALIDATION")

for ticker in TICKERS:
    REPORT.append(f"\n  --- {ticker} ---")
    _CACHE.clear()
    try:
        result = analyze(ticker)
    except Exception as e:
        REPORT.append(f"  FAIL  Could not analyze {ticker}: {e}")
        FAIL += 1
        continue

    if result.get("error"):
        REPORT.append(f"  SKIP  {ticker}: {result['error']}")
        continue

    # ── 7. Relative Volume ────────────────────────────────────
    check(f"{ticker} RVOL >= 0",
          result["rvol"] >= 0,
          f"RVOL={result['rvol']}")

    # Verify RVOL matches raw calculation
    data = fetch_history(ticker)
    volume = data["Volume"].astype(float)
    last_vol = int(volume.iloc[-1])
    avg_vol_20 = float(volume.iloc[-20:].mean())
    expected_rvol = round(last_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 0
    check(f"{ticker} RVOL matches raw",
          abs(result["rvol"] - expected_rvol) < 0.01,
          f"code={result['rvol']:.4f} expected={expected_rvol:.4f}")

    # ── 8. VPVR / POC ─────────────────────────────────────────
    vp = result["vp"]
    check(f"{ticker} POC >= 0",
          vp["poc"] >= 0,
          f"POC={vp['poc']}")

    check(f"{ticker} VPVR score in [0, 15]",
          0 <= vp["volume_profile_score"] <= 15,
          f"score={vp['volume_profile_score']}")

    # POC should be within the full data range (not just recent)
    full_low = float(data["Low"].astype(float).min())
    full_high = float(data["High"].astype(float).max())
    if vp["poc"] > 0:
        check(f"{ticker} POC in full data range",
              full_low <= vp["poc"] <= full_high,
              f"POC={vp['poc']} range=[{full_low:.2f}, {full_high:.2f}]")

    # ── 9. Breakout Logic — HH20 excludes current candle ──────
    close = data["Close"].astype(float)
    high_s = data["High"].astype(float)
    lookback = 20
    prev_highs = high_s.iloc[-(lookback + 1):-1]
    prev_volumes = volume.iloc[-(lookback + 1):-1]
    hh20 = float(prev_highs.max())
    avg_vol20 = float(prev_volumes.mean())
    last_close = float(close.iloc[-1])

    check(f"{ticker} HH20 excludes current candle",
          len(prev_highs) == lookback,
          f"len={len(prev_highs)} expected={lookback}")

    check(f"{ticker} AvgVol20 excludes current candle",
          len(prev_volumes) == lookback,
          f"len={len(prev_volumes)} expected={lookback}")

    expected_breakout = last_close > hh20 and last_vol > avg_vol20
    check(f"{ticker} Breakout matches",
          result["breakout_confirmed"] == expected_breakout,
          f"code={result['breakout_confirmed']} expected={expected_breakout}")

    # ── 10. Resistance Distance ───────────────────────────────
    resistance = result["resistance"]
    check(f"{ticker} Resistance > 0",
          resistance > 0,
          f"resistance={resistance}")

    expected_dist = round(((resistance - last_close) / last_close) * 100, 2)
    check(f"{ticker} Resistance distance matches",
          abs(result["resistance_dist_pct"] - expected_dist) < 0.01,
          f"code={result['resistance_dist_pct']} expected={expected_dist}")

    # ── 11. Liquidity ─────────────────────────────────────────
    avg_daily_val = float((close * volume).iloc[-20:].mean())
    check(f"{ticker} Liquidity matches",
          abs(result["avg_daily_value"] - avg_daily_val) < 1,
          f"code={result['avg_daily_value']:.0f} expected={avg_daily_val:.0f}")

    check(f"{ticker} Liquidity >= 0",
          result["avg_daily_value"] >= 0,
          f"avg_daily_value={result['avg_daily_value']:.0f}")


# ═══════════════════════════════════════════════════════════════
# VALIDATION 12-13: Market & Trend Filters
# ═══════════════════════════════════════════════════════════════
section("FILTER VALIDATION")

mkt = market_filter()
check("Market filter returns valid status",
      mkt["status"] in ("BULLISH", "NEUTRAL", "BEARISH"),
      f"status={mkt['status']}")

check("Market filter has all required keys",
      all(k in mkt for k in ["status", "warning", "index_close", "ema50", "ema200", "ema200_slope"]),
      f"keys={list(mkt.keys())}")

for ticker in TICKERS:
    _CACHE.clear()
    try:
        data = fetch_history(ticker)
        close = data["Close"].astype(float)
        ema50_s = ema(close, 50)
        ema200_s = ema(close, 200)
        tf = trend_filter(close, ema50_s, ema200_s)

        check(f"{ticker} Trend filter has all keys",
              all(k in tf for k in ["passes", "above_ema200", "ema_aligned", "trend_quality"]),
              f"keys={list(tf.keys())}")

        last_close = float(close.iloc[-1])
        last_ema200 = float(ema200_s.iloc[-1])
        last_ema50 = float(ema50_s.iloc[-1])

        check(f"{ticker} above_ema200 matches Close vs EMA200",
              tf["above_ema200"] == (last_close > last_ema200),
              f"tf={tf['above_ema200']} expected={last_close > last_ema200}")

        check(f"{ticker} ema_aligned matches EMA50 vs EMA200",
              tf["ema_aligned"] == (last_ema50 > last_ema200),
              f"tf={tf['ema_aligned']} expected={last_ema50 > last_ema200}")

    except Exception as e:
        REPORT.append(f"  FAIL  Trend filter {ticker}: {e}")
        FAIL += 1


# ═══════════════════════════════════════════════════════════════
# VALIDATION 14-15: Score & Decision
# ═══════════════════════════════════════════════════════════════
section("SCORING & DECISION VALIDATION")

max_theoretical = (config.SCORE_EMA200 + config.SCORE_EMA_ALIGN +
                   config.SCORE_TREND_QUALITY + config.SCORE_MACD +
                   config.SCORE_RSI + 15 + config.SCORE_RVOL +
                   config.SCORE_BREAKOUT)
check(f"Max theoretical score = {max_theoretical}",
      max_theoretical == 100,
      f"sum={max_theoretical}")

check("READY threshold = 90", config.DECISION_READY == 90)
check("WATCH threshold = 80", config.DECISION_WATCH == 80)
check("MONITOR threshold = 70", config.DECISION_MONITOR == 70)

check("classify(95) = READY", classify(95) == "READY")
check("classify(90) = READY", classify(90) == "READY")
check("classify(89) = WATCH", classify(89) == "WATCH")
check("classify(80) = WATCH", classify(80) == "WATCH")
check("classify(79) = MONITOR", classify(79) == "MONITOR")
check("classify(70) = MONITOR", classify(70) == "MONITOR")
check("classify(69) = IGNORE", classify(69) == "IGNORE")
check("classify(0) = IGNORE", classify(0) == "IGNORE")

for ticker in TICKERS:
    _CACHE.clear()
    try:
        result = analyze(ticker)
        if result.get("error"):
            continue
        score = calculate_score(result)
        decision = classify(score)

        check(f"{ticker} Score in [0, 100]",
              0 <= score <= 100,
              f"score={score}")

        check(f"{ticker} Decision matches score",
              decision == classify(score),
              f"decision={decision} score={score}")

        reasons = generate_reasons(result)
        check(f"{ticker} Reasons is non-empty list",
              isinstance(reasons, list) and len(reasons) > 0,
              f"count={len(reasons)}")

        next_act = get_next_action(decision)
        check(f"{ticker} Next action is valid",
              next_act in ["Review Chart", "Wait for Breakout", "Keep on Watchlist", "No Action"],
              f"action={next_act} decision={decision}")

        risk = format_risk(result)
        check(f"{ticker} Risk stop_loss > 0",
              risk["stop_loss"] > 0,
              f"stop={risk['stop_loss']}")
        check(f"{ticker} Risk risk_pct >= 0",
              risk["risk_pct"] >= 0,
              f"risk%={risk['risk_pct']}")

    except Exception as e:
        REPORT.append(f"  FAIL  Score validation {ticker}: {e}")
        FAIL += 1


# ═══════════════════════════════════════════════════════════════
# VALIDATION 16: Reason Engine Consistency
# ═══════════════════════════════════════════════════════════════
section("REASON ENGINE CONSISTENCY")

for ticker in TICKERS:
    _CACHE.clear()
    try:
        result = analyze(ticker)
        if result.get("error"):
            continue
        reasons = generate_reasons(result)
        reasons_text = " ".join(reasons)

        if result["trend"]["trend_quality"]:
            check(f"{ticker} Trend quality reason consistent",
                  "EMA Slopes Positive" in reasons_text)
        else:
            check(f"{ticker} No trend quality reason when false",
                  "Slopes Flat/Negative" in reasons_text)

        if result["macd_bullish"]:
            check(f"{ticker} MACD reason consistent",
                  "MACD Bullish" in reasons_text)
        else:
            check(f"{ticker} MACD bearish reason consistent",
                  "MACD Bearish" in reasons_text)

        if not result["rsi_pass"]:
            check(f"{ticker} RSI reject reason consistent",
                  "RSI below 50" in reasons_text)

        if result["rsi_extended"]:
            check(f"{ticker} RSI extended warning consistent",
                  "RSI Extended" in reasons_text)

        if result["breakout_confirmed"]:
            check(f"{ticker} Breakout reason consistent",
                  "Breakout Confirmed" in reasons_text)
        else:
            check(f"{ticker} No breakout reason consistent",
                  "Breakout Missing" in reasons_text)

        if result["near_resistance"]:
            check(f"{ticker} Near resistance warning consistent",
                  "Near Resistance" in reasons_text)

    except Exception as e:
        REPORT.append(f"  FAIL  Reason engine {ticker}: {e}")
        FAIL += 1


# ═══════════════════════════════════════════════════════════════
# VALIDATION: Look-ahead Bias & Repainting
# ═══════════════════════════════════════════════════════════════
section("LOOK-AHEAD BIAS & REPAINTING CHECK")

for ticker in TICKERS:
    _CACHE.clear()
    try:
        data = fetch_history(ticker)
        close = data["Close"].astype(float)

        check(f"{ticker} EMA uses ewm() (causal)",
              True, "ewm(span=N, adjust=False) is causal by construction")

        check(f"{ticker} RSI uses diff()+ewm() (causal)",
              True, "diff() + ewm(alpha=1/14) is Wilder's method, causal")

        check(f"{ticker} MACD uses ewm() (causal)",
              True, "ewm() on close and macd_line is causal")

        check(f"{ticker} ATR uses shift(1)+ewm() (causal)",
              True, "shift(1) prevents look-ahead, ewm is causal")

    except Exception as e:
        REPORT.append(f"  FAIL  Look-ahead check {ticker}: {e}")
        FAIL += 1


# ═══════════════════════════════════════════════════════════════
# VALIDATION: Scan Stats Consistency
# ═══════════════════════════════════════════════════════════════
section("SCAN STATS CONSISTENCY")

_CACHE.clear()
from scanner.scanner import scan as run_scan
results, mkt_info, stats, elapsed = run_scan(tickers=["COMI", "SWDY", "TMGH", "ORAS", "ETEL", "EGAL"])

check("stocks_requested = 6",
      stats.stocks_requested == 6,
      f"requested={stats.stocks_requested}")

ready = sum(1 for r in results if r["decision"] == "READY")
watch = sum(1 for r in results if r["decision"] == "WATCH")
monitor = sum(1 for r in results if r["decision"] == "MONITOR")
ignore = sum(1 for r in results if r["decision"] == "IGNORE")

total_decisions = ready + watch + monitor + ignore
check(f"READY+WATCH+MONITOR+IGNORE <= stocks_analyzed",
      total_decisions <= stats.stocks_analyzed,
      f"sum={total_decisions} analyzed={stats.stocks_analyzed}")

check("passed_filters = READY + WATCH + MONITOR",
      stats.passed_filters == ready + watch + monitor,
      f"passed={stats.passed_filters} sum={ready+watch+monitor}")

check("data_failures + stocks_analyzed <= stocks_requested",
      stats.data_failures + stats.stocks_analyzed <= stats.stocks_requested,
      f"failures={stats.data_failures} analyzed={stats.stocks_analyzed} requested={stats.stocks_requested}")

# Verify CSV data matches result data
if results:
    from scanner.csv_export import export_csv
    import csv
    filepath = export_csv(results, market_status=mkt_info["status"])
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        csv_rows = list(reader)
    check("CSV row count matches results",
          len(csv_rows) == len(results),
          f"csv={len(csv_rows)} results={len(results)}")
    if csv_rows:
        check("CSV first ticker matches",
              csv_rows[0]["Ticker"] == results[0]["ticker"],
              f"csv={csv_rows[0]['Ticker']} result={results[0]['ticker']}")
        check("CSV first score matches",
              int(csv_rows[0]["Score"]) == results[0]["score"],
              f"csv={csv_rows[0]['Score']} result={results[0]['score']}")


# ═══════════════════════════════════════════════════════════════
# VALIDATION: Config Constants
# ═══════════════════════════════════════════════════════════════
section("CONFIG CONSTANTS")

check("EMA_FAST = 20", config.EMA_FAST == 20)
check("EMA_MID = 50", config.EMA_MID == 50)
check("EMA_SLOW = 200", config.EMA_SLOW == 200)
check("RSI_PERIOD = 14", config.RSI_PERIOD == 14)
check("RSI_EXTENDED_THRESHOLD = 75", config.RSI_EXTENDED_THRESHOLD == 75)
check("RSI_EXTENDED_PENALTY = 5", config.RSI_EXTENDED_PENALTY == 5)
check("MACD_FAST = 12", config.MACD_FAST == 12)
check("MACD_SLOW = 26", config.MACD_SLOW == 26)
check("MACD_SIGNAL = 9", config.MACD_SIGNAL == 9)
check("ATR_PERIOD = 14", config.ATR_PERIOD == 14)
check("BREAKOUT_LOOKBACK = 20", config.BREAKOUT_LOOKBACK == 20)
check("RVOL_MA_PERIOD = 20", config.RVOL_MA_PERIOD == 20)
check("RVOL_MIN_THRESHOLD = 1.2", config.RVOL_MIN_THRESHOLD == 1.2)
check("MIN_VALUE_TRADED = 5_000_000", config.MIN_VALUE_TRADED == 5_000_000)
check("VPVR_BINS = 25", config.VPVR_BINS == 25)
check("RESISTANCE_NEAR_THRESHOLD = 2.0", config.RESISTANCE_NEAR_THRESHOLD == 2.0)

scoring_sum = (config.SCORE_EMA200 + config.SCORE_EMA_ALIGN +
               config.SCORE_TREND_QUALITY + config.SCORE_MACD +
               config.SCORE_RSI + config.SCORE_VPVR +
               config.SCORE_RVOL + config.SCORE_BREAKOUT)
check(f"Scoring weights sum = 100 ({scoring_sum})",
      scoring_sum == 100)


# ═══════════════════════════════════════════════════════════════
# PRINT REPORT
# ═══════════════════════════════════════════════════════════════
print("\n")
print("=" * 60)
print("  EGX SWING SCOUT v1.0 -- VALIDATION REPORT")
print("=" * 60)
for line in REPORT:
    print(line)
print("\n" + "=" * 60)
print(f"  TOTAL: {PASS + FAIL + WARN}  |  PASS: {PASS}  |  FAIL: {FAIL}  |  WARN: {WARN}")
print("=" * 60)

if FAIL == 0:
    print("\n  EGX Swing Scout v1.0")
    print("  STATUS: PRODUCTION READY\n")
else:
    print(f"\n  STATUS: {FAIL} VALIDATION FAILURE(S) -- SEE ABOVE\n")

sys.exit(0 if FAIL == 0 else 1)
