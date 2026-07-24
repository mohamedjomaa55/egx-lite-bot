"""
EGX Lite Market Radar — Render Web Server
Keeps the service alive, runs the Telegram bot, and serves the dashboard.
"""

import os
import sys
import json
import time
import logging
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, send_from_directory, request

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

HISTORY_FILE = Path(__file__).parent / "data" / "scan_history.json"
HISTORY_FILE.parent.mkdir(exist_ok=True)

_lock = threading.Lock()
_cached_result = None
_cached_timestamp = None
_CACHE_TTL = 300


def _result_to_dict(result):
    from scanner.market_radar import ActivityCategory
    items = []
    for item in result.all_items:
        items.append({
            "symbol": item.symbol,
            "company_name": item.company_name,
            "latest_close": item.latest_close,
            "previous_close": item.previous_close,
            "session_open": item.session_open,
            "session_high": item.session_high,
            "session_low": item.session_low,
            "display_price": item.display_price,
            "price_date": item.price_date,
            "price_change_percent": item.price_change_percent,
            "volume": item.volume,
            "average_volume_20": item.average_volume_20,
            "rvol_20": item.rvol_20,
            "traded_value": item.traded_value,
            "average_traded_value_20": item.average_traded_value_20,
            "rsi_14": item.rsi_14,
            "rsi_change": item.rsi_change,
            "macd_histogram": item.macd_histogram,
            "macd_histogram_change": item.macd_histogram_change,
            "activity_score": item.activity_score,
            "activity_category": item.activity_category,
            "activity_level": item.activity_level,
            "activity_label": item.activity_label,
            "reasons": item.reasons,
            "close_location_value": item.close_location_value,
            "candle_body_percent": item.candle_body_percent,
            "volume_percentile_60": item.volume_percentile_60,
            "price_return_5d": item.price_return_5d,
            "price_return_20d": item.price_return_20d,
            "adx_14": item.adx_14,
            "provider_latest_date": item.provider_latest_date,
            "expected_latest_session": item.expected_latest_session,
            "freshness_status": item.freshness_status,
            "freshness_note": item.freshness_note,
            "freshness_delay_days": item.freshness_delay_days,
            "data_mode": item.data_mode,
        })

    return {
        "timestamp": result.timestamp,
        "data_date": result.data_date,
        "expected_latest_session": result.expected_latest_session,
        "freshness_status": result.freshness_status,
        "freshness_note": result.freshness_note,
        "freshness_delay_days": result.freshness_delay_days,
        "provider_latest_date": result.items[0].provider_latest_date if result.items else "",
        "stats": {
            "symbols_scanned": result.stats.symbols_scanned,
            "activity_detected": result.stats.activity_detected,
            "buying_count": result.stats.buying_count,
            "selling_count": result.stats.selling_count,
            "unusual_count": result.stats.unusual_count,
            "failed_count": result.stats.failed_count,
            "skipped_illiquid": result.stats.skipped_illiquid,
            "scan_duration": result.stats.scan_duration,
        },
        "items": items,
    }


def _run_scan():
    from scanner.market_radar import run_market_radar
    return run_market_radar(top_n=50)


def _run_scan_fresh():
    from scanner.market_radar import run_market_radar
    from scanner.data_provider import clear_cache
    clear_cache()
    return run_market_radar(top_n=50)


def _save_history(result_dict):
    try:
        history = []
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)

        entry = {
            "timestamp": result_dict.get("timestamp", ""),
            "scanned": result_dict["stats"]["symbols_scanned"],
            "activity": result_dict["stats"]["activity_detected"],
            "buying": result_dict["stats"]["buying_count"],
            "selling": result_dict["stats"]["selling_count"],
            "unusual": result_dict["stats"]["unusual_count"],
            "duration": result_dict["stats"]["scan_duration"],
            "freshness_status": result_dict.get("freshness_status", ""),
        }
        history.insert(0, entry)
        history = history[:100]

        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("Failed to save history: %s", exc)


# ─── Dashboard Routes ───────────────────────────────────────────────
@app.route("/")
def home():
    return "EGX Lite Market Radar is running! <a href='/dashboard'>Dashboard</a>"


@app.route("/health")
def health():
    return {"status": "ok", "service": "egx-lite-market-radar"}


@app.route("/dashboard")
def dashboard():
    return send_from_directory("dashboard", "index.html")


@app.route("/dashboard/<path:filename>")
def dashboard_static(filename):
    return send_from_directory("dashboard", filename)


# ─── API Routes ─────────────────────────────────────────────────────
@app.route("/api/radar")
def api_radar():
    global _cached_result, _cached_timestamp

    with _lock:
        if _cached_result is not None and _cached_timestamp is not None:
            age = time.time() - _cached_timestamp
            if age < _CACHE_TTL:
                return jsonify(_cached_result)

    result = _run_scan_fresh()
    result_dict = _result_to_dict(result)
    _save_history(result_dict)

    with _lock:
        _cached_result = result_dict
        _cached_timestamp = time.time()

    return jsonify(result_dict)


@app.route("/api/radar/refresh", methods=["POST"])
def api_radar_refresh():
    global _cached_result, _cached_timestamp

    result = _run_scan_fresh()
    result_dict = _result_to_dict(result)
    _save_history(result_dict)

    with _lock:
        _cached_result = result_dict
        _cached_timestamp = time.time()

    return jsonify(result_dict)


@app.route("/api/history")
def api_history():
    try:
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return jsonify(json.load(f))
    except Exception:
        pass
    return jsonify([])


# ─── Run ────────────────────────────────────────────────────────────
def run_flask():
    port = int(os.getenv("PORT", 5000))
    logger.info("Starting web server on port %d", port)
    app.run(host="0.0.0.0", port=port, use_reloader=False)


if __name__ == "__main__":
    run_flask()
