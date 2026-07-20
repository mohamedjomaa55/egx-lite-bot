"""
CSV Export — Step 17.
"""

import csv
import os
from datetime import datetime

from . import config


def export_csv(results: list[dict], market_status: str, output_dir: str = "output") -> str:
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(output_dir, f"egx_scan_{timestamp}.csv")

    headers = [
        "Ticker", "Name", "Close", "Score", "Decision", "Market Status",
        "Trend", "MACD", "RSI", "RVOL", "POC Distance%", "Resistance Distance%",
        "ATR", "Risk%", "Warnings", "Next Action", "Reasons",
    ]

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for r in results:
            trend_str = "Bullish" if r["trend"]["above_ema200"] and r["trend"]["ema_aligned"] else \
                        "Bearish" if not r["trend"]["above_ema200"] else "Mixed"
            macd_str = "Bullish" if r["macd_bullish"] else "Bearish"

            warnings = []
            if r["rsi_extended"]:
                warnings.append("RSI Extended")
            if r["near_resistance"]:
                warnings.append("Near Resistance")
            warnings_str = "; ".join(warnings) if warnings else ""

            reasons_str = " | ".join(r.get("reasons", []))
            name = config.STOCK_NAMES.get(r["ticker"], "")

            writer.writerow([
                r["ticker"],
                name,
                r["close"],
                r["score"],
                r["decision"],
                market_status,
                trend_str,
                macd_str,
                r["rsi"],
                r["rvol"],
                r["vp"]["distance_from_poc_pct"],
                r["resistance_dist_pct"],
                r["atr"],
                r["risk_pct"],
                warnings_str,
                r["next_action"],
                reasons_str,
            ])

    return filepath


def export_failed_tickers(failed: list[dict], output_dir: str = "output") -> str:
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(output_dir, f"failed_tickers_{timestamp}.csv")

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["Ticker", "Reason"])
        for item in failed:
            writer.writerow([item["ticker"], item["reason"]])

    return filepath
