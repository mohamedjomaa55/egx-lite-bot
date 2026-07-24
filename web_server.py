"""
EGX Lite Market Radar — Render Web Server
Keeps the service alive, runs the Telegram bot, and serves the dashboard.

Background scan architecture:
  - Scans run in background threads, never blocking HTTP requests.
  - /api/radar serves from cache only (202 when empty, 200 fresh/stale).
  - /api/radar/refresh triggers background scan, returns 202 immediately.
  - Periodic scheduler re-scans at configured interval.
"""

import copy
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
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
    level=logging.INFO,
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

HISTORY_FILE = Path(__file__).parent / "data" / "scan_history.json"
HISTORY_FILE.parent.mkdir(exist_ok=True)

_lock = threading.Lock()
_scan_lock = threading.Lock()
_cached_result = None
_cached_timestamp = None
_CACHE_TTL = 300
_scan_running = False


class _RateLimiter:
    """Simple token-bucket rate limiter per key."""

    def __init__(self):
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, rpm: int) -> bool:
        if rpm <= 0:
            return True
        now = time.time()
        window = 60.0
        with self._lock:
            timestamps = self._buckets.setdefault(key, [])
            cutoff = now - window
            self._buckets[key] = [t for t in timestamps if t > cutoff]
            if len(self._buckets[key]) >= rpm:
                return False
            self._buckets[key].append(now)
            return True


_rate_limiter = _RateLimiter()


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


# ─── Background Scan ─────────────────────────────────────────────────
def _background_scan(label="periodic"):
    """Run a full scan in the background thread.

    Acquires _scan_lock non-blocking so only one scan runs at a time.
    Updates cache and history only on success. Always releases the lock
    and resets _scan_running in finally.
    """
    global _cached_result, _cached_timestamp, _scan_running

    acquired = _scan_lock.acquire(blocking=False)
    if not acquired:
        logger.info("Scan already active, skipping (%s)", label)
        return False

    try:
        with _lock:
            _scan_running = True

        result = _run_scan_fresh()
        result_dict = _result_to_dict(result)
        _save_history(result_dict)

        with _lock:
            _cached_result = copy.deepcopy(result_dict)
            _cached_timestamp = time.time()

        logger.info("Background scan completed (%s)", label)
        return True
    except Exception as e:
        logger.error("Background scan failed (%s): %s", label, e)
        return False
    finally:
        with _lock:
            _scan_running = False
        if acquired:
            _scan_lock.release()


def _periodic_scheduler():
    """Run background scans on a configured interval. Never terminates."""
    from scanner import config
    while True:
        try:
            time.sleep(config.SCAN_INTERVAL_MINUTES * 60)
            _background_scan("periodic")
        except Exception as e:
            logger.error("Scheduler error: %s", e)


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
    global _scan_running

    if not _rate_limiter.allow("api:radar", int(os.getenv("RATE_LIMIT_API_RADAR_RPM", "30"))):
        return jsonify({"error": "Rate limit exceeded"}), 429

    with _lock:
        cached = copy.deepcopy(_cached_result) if _cached_result is not None else None
        cached_ts = _cached_timestamp
        running = _scan_running

    if cached is None:
        if not running:
            threading.Thread(target=_background_scan, args=("api-trigger",), daemon=True).start()
        return jsonify({"status": "initial_scan_in_progress", "scan_running": True}), 202

    age = time.time() - cached_ts if cached_ts else float("inf")

    if age < _CACHE_TTL:
        return jsonify(cached)

    if not running:
        threading.Thread(target=_background_scan, args=("api-trigger",), daemon=True).start()

    cached["stale"] = True
    cached["cache_timestamp"] = cached_ts
    cached["scan_running"] = running
    return jsonify(cached), 200


@app.route("/api/radar/refresh", methods=["POST"])
def api_radar_refresh():
    global _scan_running

    admin_key = os.getenv("ADMIN_API_KEY", "")
    dev_bypass = os.getenv("ALLOW_DEV_SERVER_FALLBACK", "false").lower() == "true"

    if not admin_key:
        if not dev_bypass:
            return jsonify({"error": "Admin API key not configured"}), 503
    else:
        provided = request.headers.get("X-API-Key", "")
        if not provided:
            return jsonify({"error": "Missing API key"}), 401
        if provided != admin_key:
            return jsonify({"error": "Invalid API key"}), 403

    if not _rate_limiter.allow("api:refresh", int(os.getenv("RATE_LIMIT_API_REFRESH_RPM", "5"))):
        return jsonify({"error": "Rate limit exceeded"}), 429

    with _lock:
        if _scan_running:
            return jsonify({"error": "Scan already in progress"}), 409

    threading.Thread(target=_background_scan, args=("manual",), daemon=True).start()
    return jsonify({"status": "scan triggered"}), 202


@app.route("/api/history")
def api_history():
    if not _rate_limiter.allow("api:history", int(os.getenv("RATE_LIMIT_API_HISTORY_RPM", "60"))):
        return jsonify({"error": "Rate limit exceeded"}), 429

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
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=4)
    except ImportError:
        dev_bypass = os.getenv("ALLOW_DEV_SERVER_FALLBACK", "false").lower() == "true"
        if dev_bypass:
            logger.warning("waitress not installed — falling back to Flask dev server (ALLOW_DEV_SERVER_FALLBACK=true)")
            app.run(host="0.0.0.0", port=port, use_reloader=False)
        else:
            logger.error("waitress not installed and ALLOW_DEV_SERVER_FALLBACK is not 'true' — aborting startup")
            raise SystemExit("waitress is required in production. Install waitress or set ALLOW_DEV_SERVER_FALLBACK=true for development.")


if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask server started in background")

    initial_scan = threading.Thread(target=_background_scan, args=("startup",), daemon=True)
    initial_scan.start()
    logger.info("Initial background scan started")

    scheduler = threading.Thread(target=_periodic_scheduler, daemon=True)
    scheduler.start()
    from scanner import config as _cfg
    logger.info("Periodic scheduler started (interval: %d min)", _cfg.SCAN_INTERVAL_MINUTES)

    logger.info("Starting Telegram bot in main thread...")
    from bot import main as bot_main
    bot_main()
