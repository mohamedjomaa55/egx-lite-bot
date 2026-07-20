"""
Scanner Engine — Orchestrates the full scan pipeline.
"""

import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config
from .data_provider import get_all_tickers
from .filters import market_filter
from .technical_analysis import analyze
from .scoring import calculate_score
from .decisions import classify
from .reason_engine import generate_reasons, get_next_action
from .risk import format_risk

logger = logging.getLogger(__name__)


class ScanStats:
    def __init__(self):
        self.stocks_requested = 0
        self.stocks_analyzed = 0
        self.data_failures = 0
        self.passed_filters = 0
        self.failed_tickers: list[dict] = []

    def to_dict(self) -> dict:
        return {
            "stocks_requested": self.stocks_requested,
            "stocks_analyzed": self.stocks_analyzed,
            "data_failures": self.data_failures,
            "passed_filters": self.passed_filters,
            "failed_tickers": self.failed_tickers,
        }


def scan(tickers: list[str] = None, max_workers: int = 8) -> tuple[list[dict], dict, ScanStats, float]:
    """
    Run the full EGX Swing Scout scan.
    Returns (results, market_info, scan_stats, elapsed_time).
    """
    mkt = market_filter()

    if tickers is None:
        tickers = get_all_tickers()

    stats = ScanStats()
    stats.stocks_requested = len(tickers)

    start = time.time()
    results = []
    all_results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ticker = {executor.submit(analyze, t): t for t in tickers}
        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            try:
                result = future.result()
                if result.get("error"):
                    stats.data_failures += 1
                    stats.failed_tickers.append({"ticker": ticker, "reason": result["error"]})
                    logger.warning(f"Data failure: {ticker} — {result['error']}")
                    continue

                stats.stocks_analyzed += 1

                if not result["liquidity_pass"]:
                    continue

                all_results.append(result)
            except Exception as e:
                stats.data_failures += 1
                stats.failed_tickers.append({"ticker": ticker, "reason": str(e)})
                logger.warning(f"Failed to analyze {ticker}: {e}")

    elapsed = time.time() - start

    for result in all_results:
        score = calculate_score(result)
        decision = classify(score)
        reasons = generate_reasons(result)
        risk = format_risk(result)
        next_action = get_next_action(decision)

        result["score"] = score
        result["decision"] = decision
        result["reasons"] = reasons
        result["next_action"] = next_action
        result["suggested_stop"] = risk["stop_loss"]
        result["risk_pct"] = risk["risk_pct"]

        results.append(result)

    results.sort(key=lambda x: x["score"], reverse=True)

    stats.passed_filters = sum(
        1 for r in results if r["decision"] in ("READY", "WATCH", "MONITOR")
    )

    return results, mkt, stats, elapsed
