"""
EGXAPI Evaluation Script
========================

Tests the shadow EGXAPI provider against 6 symbols and produces
an evaluation report.  Run during market hours for best results.

Usage
-----
    python -m providers.eval

Environment
-----------
    EGXAPI_KEY  — Required.  Paper API key.
    EGXAPI_ENV  — "paper" (default).

Report is saved to reports/egxapi_eval_YYYYMMDD_HHMMSS.txt
"""

import os
import sys
import time
import logging
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Setup paths ───────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from providers.egxapi_provider import (
    EGXAPIProvider,
    QuoteState,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("eval")

# ── Config ────────────────────────────────────────────────────────────
TEST_SYMBOLS = ["ARCC", "EGAL", "TMGH", "ETEL", "COMI", "FWRY"]
REPORT_DIR = Path("reports")


def mask_key(key: str) -> str:
    """Show only first 4 and last 4 chars of the API key."""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


def run_evaluation() -> str:
    """Run full evaluation and return report text."""
    lines: list[str] = []
    sep = "=" * 60

    def out(s: str = "") -> None:
        lines.append(s)
        print(s)

    # ── Header ────────────────────────────────────────────────────────
    out(sep)
    out("  EGXAPI Shadow Provider — Evaluation Report")
    out(sep)
    out()

    api_key = os.getenv("EGXAPI_KEY", "")
    env = os.getenv("EGXAPI_ENV", "paper")
    out(f"  Date      : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    out(f"  Env       : {env}")
    out(f"  Key       : {mask_key(api_key) if api_key else 'NOT SET'}")
    out(f"  Symbols   : {', '.join(TEST_SYMBOLS)}")
    out()

    if not api_key:
        out("  ❌ EGXAPI_KEY not set.  Cannot evaluate.")
        out()
        out("  Set EGXAPI_KEY in .env or environment and re-run.")
        return "\n".join(lines)

    # ── Initialize provider ───────────────────────────────────────────
    provider = EGXAPIProvider()

    # ── Availability check ────────────────────────────────────────────
    out("─── Connectivity Check ───")
    t0 = time.monotonic()
    available = provider.is_available()
    connect_ms = (time.monotonic() - t0) * 1000
    if available:
        out(f"  ✅ EGXAPI reachable ({connect_ms:.0f}ms)")
    else:
        out(f"  ❌ EGXAPI unreachable ({connect_ms:.0f}ms)")
        out()
        out("  Possible causes:")
        out("    - Invalid API key")
        out("    - Network issue")
        out("    - EGXAPI service down")
        out()
        out("  Check key at https://egxapi.com/dashboard")
    out()

    # ── Quote evaluation ──────────────────────────────────────────────
    out("─── Quote Evaluation ───")
    out()

    results_summary: list[dict] = []
    success_count = 0
    mismatch_count = 0
    unavailable_count = 0

    for sym in TEST_SYMBOLS:
        out(f"  {sym}:")
        comp = provider.compare_quote(sym)

        egxapi_p = comp.egxapi_price
        fallback_p = comp.fallback_price
        diff = comp.price_difference
        diff_pct = comp.price_difference_percent
        ts_age = comp.timestamp_age_seconds
        state = comp.egxapi_state

        # State emoji
        state_emoji = {
            QuoteState.LIVE_VERIFIED.value: "🟢",
            QuoteState.DELAYED.value: "🟡",
            QuoteState.PRICE_MISMATCH.value: "🔴",
            QuoteState.INVALID_SYMBOL.value: "⚫",
            QuoteState.DATA_UNAVAILABLE.value: "⚫",
        }.get(state, "⚪")

        if state == QuoteState.LIVE_VERIFIED.value:
            success_count += 1
        elif state == QuoteState.PRICE_MISMATCH.value:
            mismatch_count += 1
        else:
            unavailable_count += 1

        out(f"    EGXAPI Price  : {egxapi_p if egxapi_p is not None else 'N/A'}")
        out(f"    Fallback Price: {fallback_p if fallback_p is not None else 'N/A'} (source: {comp.fallback_source})")

        if diff is not None:
            out(f"    Difference    : {diff:+.4f} ({diff_pct:+.2f}%)")
        else:
            out(f"    Difference    : N/A")

        if ts_age is not None:
            out(f"    Timestamp Age : {ts_age:.1f}s")
        else:
            out(f"    Timestamp Age : N/A")

        out(f"    Bid/Ask       : {comp.egxapi_bid}/{comp.egxapi_ask}")
        out(f"    Volume        : {comp.egxapi_volume}")
        out(f"    State         : {state_emoji} {state}")
        out()

        results_summary.append({
            "symbol": sym,
            "egxapi_price": egxapi_p,
            "fallback_price": fallback_p,
            "diff_pct": diff_pct,
            "state": state,
        })

        # Small delay to avoid rate limits
        time.sleep(0.3)

    # ── Intrabar test (first symbol only) ─────────────────────────────
    out("─── Intraday Bars (first symbol) ───")
    first_sym = TEST_SYMBOLS[0]
    bars = provider.get_intraday_bars(first_sym, interval="1m", limit=5)
    if bars:
        out(f"  ✅ {first_sym}: {len(bars)} bars returned")
        for b in bars[-3:]:
            out(f"    {b.timestamp}  O={b.open} H={b.high} L={b.low} C={b.close} V={b.volume}")
    else:
        out(f"  ⚠️  {first_sym}: No intraday bars returned")
        out(f"      (May not be available during off-hours or without proper subscription)")
    out()

    # ── Trades test (first symbol only) ───────────────────────────────
    out("─── Recent Trades (first symbol) ───")
    trades = provider.get_trades(first_sym, limit=5)
    if trades:
        out(f"  ✅ {first_sym}: {len(trades)} trades returned")
        for t in trades[-3:]:
            out(f"    {t.timestamp}  price={t.price} size={t.size} side={t.side}")
    else:
        out(f"  ⚠️  {first_sym}: No trades returned")
        out(f"      (May not be available during off-hours)")
    out()

    # ── Order blocking test ───────────────────────────────────────────
    out("─── Order Blocking Verification ───")
    try:
        provider.create_order()
        out("  ❌ SECURITY: create_order did NOT raise!")
    except NotImplementedError:
        out("  ✅ create_order correctly blocked")
    except Exception as e:
        out(f"  ✅ create_order raised {type(e).__name__}: {e}")

    try:
        provider.cancel_order()
        out("  ❌ SECURITY: cancel_order did NOT raise!")
    except NotImplementedError:
        out("  ✅ cancel_order correctly blocked")
    except Exception as e:
        out(f"  ✅ cancel_order raised {type(e).__name__}: {e}")
    out()

    # ── Summary ───────────────────────────────────────────────────────
    out(sep)
    out("  Summary")
    out(sep)
    out()
    out(f"  Total symbols tested : {len(TEST_SYMBOLS)}")
    out(f"  Live Verified        : {success_count}")
    out(f"  Price Mismatch       : {mismatch_count}")
    out(f"  Data Unavailable     : {unavailable_count}")
    out()

    # Accuracy assessment
    matched = [r for r in results_summary if r["diff_pct"] is not None and abs(r["diff_pct"]) <= 1.0]
    out(f"  Price match (<=1%): {len(matched)}/{len(TEST_SYMBOLS)}")

    if matched:
        avg_diff = sum(abs(r["diff_pct"]) for r in matched) / len(matched)
        out(f"  Average diff      : {avg_diff:.2f}%")
    out()

    if unavailable_count == len(TEST_SYMBOLS):
        out("  ⚠️  No data from EGXAPI.  Possible causes:")
        out("    1. API key invalid or expired")
        out("    2. Market is closed (EGX: Sun-Thu 10:00-14:15 Cairo)")
        out("    3. EGXAPI symbols differ from our tickers")
        out("    4. Subscription tier does not include market data")
        out()
        out("  Next steps:")
        out("    - Verify key at https://egxapi.com/dashboard")
        out("    - Re-run during EGX market hours")
        out("    - Check EGXAPI docs for correct symbol format")
    elif mismatch_count > 0:
        out("  ⚠️  Some prices don't match fallback.  Possible causes:")
        out("    - EGXAPI uses different price type (bid/ask vs last)")
        out("    - Data delay on one provider")
        out("    - Symbol format mismatch")
    else:
        out("  ✅ All available prices match within tolerance.")
    out()

    out(sep)
    out("  ⚠️  SHADOW MODE — EGXAPI data is NOT used for decisions.")
    out(sep)

    return "\n".join(lines)


def save_report(content: str) -> Path:
    """Save the report to reports/ directory."""
    REPORT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / f"egxapi_eval_{ts}.txt"
    path.write_text(content, encoding="utf-8")
    return path


if __name__ == "__main__":
    report = run_evaluation()
    path = save_report(report)
    print(f"\n📄 Report saved: {path}")
