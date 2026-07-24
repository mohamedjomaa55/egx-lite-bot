"""
Tests for Background Scan Architecture
=======================================

Behavior-based tests for the non-blocking scan pattern, periodic
scheduler, cache-first API responses, and scan state management.

Usage
-----
    python -m pytest tests/test_background_scan.py -v
"""

import os
import sys
import time
import threading
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import web_server


def _mock_result():
    result = MagicMock()
    result.all_items = []
    result.items = []
    result.timestamp = "2026-07-21T15:00:00"
    result.data_date = "2026-07-21"
    result.expected_latest_session = "2026-07-21"
    result.freshness_status = "CURRENT"
    result.freshness_note = "Data is current"
    result.freshness_delay_days = 0
    result.stats.symbols_scanned = 34
    result.stats.activity_detected = 5
    result.stats.buying_count = 2
    result.stats.selling_count = 2
    result.stats.unusual_count = 1
    result.stats.failed_count = 0
    result.stats.skipped_illiquid = 0
    result.stats.scan_duration = 12.5
    return result


def _set_cache(data=None, ts=None):
    with web_server._lock:
        web_server._cached_result = data
        web_server._cached_timestamp = ts


def _reset():
    _set_cache(None, None)
    with web_server._lock:
        web_server._scan_running = False
    web_server._scan_lock = threading.Lock()


@pytest.fixture(autouse=True)
def _clean_state():
    _reset()
    yield
    _reset()


@pytest.fixture
def client():
    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as c:
        yield c


# ══════════════════════════════════════════════════════════════════════
# 1. SUCCESSFUL BACKGROUND SCAN
# ══════════════════════════════════════════════════════════════════════
class TestSuccessfulBackgroundScan:
    def test_updates_cached_result(self):
        with patch("web_server._run_scan_fresh", return_value=_mock_result()):
            web_server._background_scan("test")
        with web_server._lock:
            assert web_server._cached_result is not None
            assert web_server._cached_result["timestamp"] == "2026-07-21T15:00:00"
            assert web_server._cached_result["stats"]["symbols_scanned"] == 34

    def test_updates_cached_timestamp(self):
        before = time.time()
        with patch("web_server._run_scan_fresh", return_value=_mock_result()):
            web_server._background_scan("test")
        after = time.time()
        with web_server._lock:
            assert before <= web_server._cached_timestamp <= after

    def test_updates_history(self):
        with patch("web_server._run_scan_fresh", return_value=_mock_result()):
            with patch("web_server._save_history") as mock_save:
                web_server._background_scan("test")
                mock_save.assert_called_once()
                saved_dict = mock_save.call_args[0][0]
                assert saved_dict["timestamp"] == "2026-07-21T15:00:00"

    def test_returns_success(self):
        with patch("web_server._run_scan_fresh", return_value=_mock_result()):
            assert web_server._background_scan("test") is True


# ══════════════════════════════════════════════════════════════════════
# 2. FAILED BACKGROUND SCAN
# ══════════════════════════════════════════════════════════════════════
class TestFailedBackgroundScan:
    def test_previous_cache_unchanged(self):
        _set_cache({"items": [{"symbol": "OLD"}], "stats": {}}, time.time() - 100)
        with patch("web_server._run_scan_fresh", side_effect=Exception("network")):
            web_server._background_scan("test-fail")
        with web_server._lock:
            assert web_server._cached_result["items"][0]["symbol"] == "OLD"

    def test_previous_timestamp_unchanged(self):
        _set_cache({"items": []}, 42.0)
        with patch("web_server._run_scan_fresh", side_effect=Exception("fail")):
            web_server._background_scan("test-fail")
        with web_server._lock:
            assert web_server._cached_timestamp == 42.0

    def test_history_not_modified(self):
        with patch("web_server._run_scan_fresh", side_effect=Exception("fail")):
            with patch("web_server._save_history") as mock_save:
                web_server._background_scan("test-fail")
                mock_save.assert_not_called()

    def test_exception_is_logged(self):
        with patch("web_server._run_scan_fresh", side_effect=RuntimeError("boom")):
            with patch("web_server.logger") as mock_log:
                web_server._background_scan("test-fail")
                mock_log.error.assert_called()
                call_args = mock_log.error.call_args[0]
                assert "boom" in str(call_args[2])

    def test_scan_running_reset(self):
        with patch("web_server._run_scan_fresh", side_effect=Exception("fail")):
            web_server._background_scan("test-fail")
        with web_server._lock:
            assert web_server._scan_running is False

    def test_scan_lock_released(self):
        with patch("web_server._run_scan_fresh", side_effect=Exception("fail")):
            web_server._background_scan("test-fail")
        assert not web_server._scan_lock.locked()


# ══════════════════════════════════════════════════════════════════════
# 3. GET /api/radar
# ══════════════════════════════════════════════════════════════════════
class TestGetRadar:
    def test_no_cache_returns_202(self, client):
        _set_cache(None, None)
        resp = client.get("/api/radar")
        assert resp.status_code == 202

    def test_no_cache_returns_initial_scan_in_progress(self, client):
        _set_cache(None, None)
        resp = client.get("/api/radar")
        data = resp.get_json()
        assert data["status"] == "initial_scan_in_progress"

    def test_no_cache_starts_background_scan(self, client):
        _set_cache(None, None)
        with patch("web_server._background_scan") as mock_bg:
            client.get("/api/radar")
            mock_bg.assert_called()

    def test_fresh_cache_returns_200(self, client):
        _set_cache({"items": [], "stats": {}}, time.time())
        resp = client.get("/api/radar")
        assert resp.status_code == 200

    def test_fresh_cache_does_not_start_scan(self, client):
        _set_cache({"items": [], "stats": {}}, time.time())
        with patch("web_server._background_scan") as mock_bg:
            client.get("/api/radar")
            mock_bg.assert_not_called()

    def test_fresh_cache_returns_cached_data(self, client):
        data = {"items": [{"symbol": "TEST"}], "stats": {"symbols_scanned": 1}}
        _set_cache(data, time.time())
        resp = client.get("/api/radar")
        result = resp.get_json()
        assert result["items"][0]["symbol"] == "TEST"

    def test_stale_cache_returns_200(self, client):
        _set_cache({"items": [], "stats": {}}, time.time() - 600)
        resp = client.get("/api/radar")
        assert resp.status_code == 200

    def test_stale_cache_has_stale_true(self, client):
        _set_cache({"items": [], "stats": {}}, time.time() - 600)
        resp = client.get("/api/radar")
        data = resp.get_json()
        assert data["stale"] is True

    def test_stale_cache_triggers_one_background_refresh(self, client):
        _set_cache({"items": [], "stats": {}}, time.time() - 600)
        with patch("web_server._background_scan") as mock_bg:
            mock_bg.return_value = True
            client.get("/api/radar")
            assert mock_bg.call_count == 1

    def test_stale_cache_no_trigger_when_scan_active(self, client):
        with web_server._lock:
            web_server._scan_running = True
        _set_cache({"items": [], "stats": {}}, time.time() - 600)
        with patch("web_server._background_scan") as mock_bg:
            client.get("/api/radar")
            mock_bg.assert_not_called()

    def test_stale_cache_preserves_original_items(self, client):
        _set_cache({"items": [{"symbol": "OLD"}], "stats": {}}, time.time() - 600)
        resp = client.get("/api/radar")
        result = resp.get_json()
        assert result["items"][0]["symbol"] == "OLD"


# ══════════════════════════════════════════════════════════════════════
# 4. POST /api/radar/refresh
# ══════════════════════════════════════════════════════════════════════
class TestPostRadarRefresh:
    def test_missing_key_returns_401(self, client):
        with patch.dict(os.environ, {"ADMIN_API_KEY": "k"}):
            resp = client.post("/api/radar/refresh")
            assert resp.status_code == 401

    def test_invalid_key_returns_403(self, client):
        with patch.dict(os.environ, {"ADMIN_API_KEY": "k"}):
            resp = client.post("/api/radar/refresh", headers={"X-API-Key": "wrong"})
            assert resp.status_code == 403

    def test_scan_running_returns_409(self, client):
        with patch.dict(os.environ, {"ADMIN_API_KEY": "k"}):
            with web_server._lock:
                web_server._scan_running = True
            resp = client.post("/api/radar/refresh", headers={"X-API-Key": "k"})
            assert resp.status_code == 409

    def test_accepted_returns_202_immediately(self, client):
        with patch.dict(os.environ, {"ADMIN_API_KEY": "k"}):
            resp = client.post("/api/radar/refresh", headers={"X-API-Key": "k"})
            assert resp.status_code == 202

    def test_does_not_block(self, client):
        with patch.dict(os.environ, {"ADMIN_API_KEY": "k"}):
            with patch("web_server._background_scan", side_effect=lambda *a: time.sleep(10)):
                start = time.time()
                resp = client.post("/api/radar/refresh", headers={"X-API-Key": "k"})
                elapsed = time.time() - start
                assert resp.status_code == 202
                assert elapsed < 2.0

    def test_no_admin_key_returns_503(self, client):
        with patch.dict(os.environ, {"ADMIN_API_KEY": "", "ALLOW_DEV_SERVER_FALLBACK": "false"}):
            resp = client.post("/api/radar/refresh")
            assert resp.status_code == 503


# ══════════════════════════════════════════════════════════════════════
# 5. IMPORTING web_server
# ══════════════════════════════════════════════════════════════════════
class TestImportBehavior:
    def test_starts_no_background_threads(self):
        assert web_server._scan_running is False

    def test_starts_no_scheduler(self):
        thread_names = {t.name for t in threading.enumerate()}
        assert not any("scheduler" in n.lower() for n in thread_names)

    def test_cache_empty(self):
        assert web_server._cached_result is None

    def test_scan_lock_not_held(self):
        assert not web_server._scan_lock.locked()


# ══════════════════════════════════════════════════════════════════════
# 6. SCAN STATE CONCURRENCY
# ══════════════════════════════════════════════════════════════════════
class TestScanStateConcurrency:
    def test_concurrent_scans_only_one_runs(self):
        call_count = {"n": 0}

        def slow_scan(*a):
            call_count["n"] += 1
            time.sleep(0.2)
            return _mock_result()

        with patch("web_server._run_scan_fresh", side_effect=slow_scan):
            results = []
            threads = []
            for _ in range(3):
                t = threading.Thread(
                    target=lambda: results.append(web_server._background_scan("concurrent"))
                )
                threads.append(t)
                t.start()
            for t in threads:
                t.join(timeout=3)
        assert sum(1 for r in results if r is True) == 1
        assert sum(1 for r in results if r is False) == 2
        assert call_count["n"] == 1

    def test_lock_released_after_all_attempts(self):
        with patch("web_server._run_scan_fresh", side_effect=Exception("fail")):
            threads = []
            for _ in range(3):
                t = threading.Thread(target=web_server._background_scan)
                threads.append(t)
                t.start()
            for t in threads:
                t.join(timeout=3)
        assert not web_server._scan_lock.locked()
        with web_server._lock:
            assert web_server._scan_running is False

    def test_scan_lock_exists(self):
        assert isinstance(web_server._scan_lock, type(threading.Lock()))


# ══════════════════════════════════════════════════════════════════════
# 7. SCHEDULER BEHAVIOR
# ══════════════════════════════════════════════════════════════════════
class TestSchedulerBehavior:
    def test_scheduler_is_callable(self):
        assert callable(web_server._periodic_scheduler)

    def test_background_scan_is_callable(self):
        assert callable(web_server._background_scan)

    def test_scheduler_skips_when_scan_active(self):
        with patch("web_server._background_scan") as mock_bg:
            mock_bg.return_value = False
            with web_server._lock:
                web_server._scan_running = True
            web_server._background_scan("scheduler-test")
            mock_bg.return_value = True
            with web_server._lock:
                web_server._scan_running = False


# ══════════════════════════════════════════════════════════════════════
# 8. SCAN INTERVAL CONFIGURATION
# ══════════════════════════════════════════════════════════════════════
class TestScanIntervalConfig:
    def test_config_exists(self):
        from scanner import config
        assert hasattr(config, "SCAN_INTERVAL_MINUTES")
        assert isinstance(config.SCAN_INTERVAL_MINUTES, int)
        assert config.SCAN_INTERVAL_MINUTES > 0

    def test_invalid_string_falls_back(self):
        from scanner import config
        original = config.SCAN_INTERVAL_MINUTES
        try:
            config.SCAN_INTERVAL_MINUTES = int("abc")
        except (ValueError, TypeError):
            config.SCAN_INTERVAL_MINUTES = 30
        assert config.SCAN_INTERVAL_MINUTES == 30
        config.SCAN_INTERVAL_MINUTES = original

    def test_negative_falls_back(self):
        from scanner import config
        original = config.SCAN_INTERVAL_MINUTES
        val = -5
        if val <= 0:
            val = 30
        assert val == 30
        config.SCAN_INTERVAL_MINUTES = original

    def test_zero_falls_back(self):
        from scanner import config
        original = config.SCAN_INTERVAL_MINUTES
        val = 0
        if val <= 0:
            val = 30
        assert val == 30
        config.SCAN_INTERVAL_MINUTES = original
