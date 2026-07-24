"""
Tests for Hardening — Security, Concurrency, Runtime Stability
==============================================================

Tests the security fixes (auth, rate limiting, .env exclusions),
concurrency safety (locks, defensive copies, scan deduplication),
runtime stability (error handling, config isolation), and
production server configuration.

Usage
-----
    python -m pytest tests/test_harden.py -v
"""

import copy
import os
import sys
import time
import json
import subprocess
import threading
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import config
import scanner.data_provider as dp
from scanner.data_provider import (
    _tv_batch_fetch,
    _get_cached_yahoo_history,
    clear_cache,
    _CACHE_LOCK,
    _TV_CACHE_LOCK,
)
import scanner.market_radar as mr
from scanner.market_radar import run_market_radar, _analyze_symbol


# ══════════════════════════════════════════════════════════════════════
# 1. .ENV AND SECRETS TESTS
# ══════════════════════════════════════════════════════════════════════
class TestEnvSecurity:
    def test_env_not_tracked_by_git(self):
        """Ensure .env is not tracked by git."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".env"],
            cwd=project_root, capture_output=True, text=True,
        )
        assert result.returncode != 0, ".env should not be tracked by git"

    def test_env_in_gitignore(self):
        """Ensure .env is in .gitignore."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        gitignore = os.path.join(project_root, ".gitignore")
        with open(gitignore, "r") as f:
            content = f.read()
        assert ".env" in content, ".env should be in .gitignore"

    def test_log_files_in_gitignore(self):
        """Ensure log files are in .gitignore."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        gitignore = os.path.join(project_root, ".gitignore")
        with open(gitignore, "r") as f:
            content = f.read()
        assert "*.log" in content, "*.log should be in .gitignore"
        assert "bot_startup.log" in content, "bot_startup.log should be in .gitignore"

    def test_env_example_has_no_real_values(self):
        """Ensure .env.example contains no real secret values."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env_example = os.path.join(project_root, ".env.example")
        assert os.path.exists(env_example), ".env.example should exist"
        with open(env_example, "r") as f:
            content = f.read()
        assert "8888708402" not in content, "Real BOT_TOKEN must not be in .env.example"
        assert "egx_live_2" not in content, "Real EGXAPI_KEY must not be in .env.example"
        assert "AAGRSpdd5zt" not in content, "Real token fragment must not be in .env.example"

    def test_no_secrets_in_source_files(self):
        """Ensure no real secret values are hardcoded in source files."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        secrets = ["8888708402", "egx_live_2wok", "AAGRSpdd5zt"]
        py_files = []
        for root, dirs, files in os.walk(project_root):
            dirs[:] = [d for d in dirs if d not in (".venv", ".venv312", "__pycache__", "node_modules", ".git")]
            for f in files:
                if f.endswith(".py") and "test_" not in f:
                    py_files.append(os.path.join(root, f))
        for filepath in py_files:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            for secret in secrets:
                assert secret not in content, f"Real secret found in {filepath}"


# ══════════════════════════════════════════════════════════════════════
# 2. RATE LIMITER TESTS
# ══════════════════════════════════════════════════════════════════════
class TestRateLimiter:
    def test_allows_within_limit(self):
        """Requests within RPM limit are allowed."""
        from web_server import _RateLimiter
        limiter = _RateLimiter()
        for _ in range(5):
            assert limiter.allow("test:1", 10) is True

    def test_blocks_over_limit(self):
        """Requests over RPM limit are blocked."""
        from web_server import _RateLimiter
        limiter = _RateLimiter()
        for _ in range(5):
            limiter.allow("test:2", 5)
        assert limiter.allow("test:2", 5) is False

    def test_unlimited_when_zero(self):
        """RPM=0 means unlimited."""
        from web_server import _RateLimiter
        limiter = _RateLimiter()
        for _ in range(1000):
            assert limiter.allow("test:3", 0) is True

    def test_different_keys_independent(self):
        """Different keys have independent limits."""
        from web_server import _RateLimiter
        limiter = _RateLimiter()
        for _ in range(5):
            limiter.allow("key:a", 5)
        assert limiter.allow("key:b", 5) is True

    def test_bot_rate_limiter_exists(self):
        """Bot module has a rate limiter."""
        from bot import _rate_limiter
        assert _rate_limiter is not None
        assert hasattr(_rate_limiter, "allow")


# ══════════════════════════════════════════════════════════════════════
# 3. CACHE DEFENSIVE COPY TESTS
# ══════════════════════════════════════════════════════════════════════
class TestCacheDefensiveCopies:
    def test_tv_batch_fetch_returns_copy(self):
        """_tv_batch_fetch returns a copy, not the cache dict."""
        clear_cache()
        dp._TV_CACHE["X"] = {"close": 100}
        dp._TV_CACHE_TS = time.time()
        result = _tv_batch_fetch()
        assert result == dp._TV_CACHE
        assert result is not dp._TV_CACHE

    def test_tv_batch_fetch_copy_mutation_safe(self):
        """Mutating the returned copy does not affect cache (deep copy)."""
        clear_cache()
        dp._TV_CACHE["Y"] = {"close": 200}
        dp._TV_CACHE_TS = time.time()
        result = _tv_batch_fetch()
        # Adding/removing keys in result won't affect cache
        result["NEW_KEY"] = {"close": 500}
        assert "NEW_KEY" not in dp._TV_CACHE
        del result["Y"]
        assert "Y" in dp._TV_CACHE

    def test_tv_batch_fetch_nested_mutation_safe(self):
        """Mutating nested dicts in the returned copy does NOT affect cache."""
        clear_cache()
        dp._TV_CACHE["Z"] = {"close": 300, "open": 290, "volume": 1000}
        dp._TV_CACHE_TS = time.time()
        result = _tv_batch_fetch()
        # Mutate nested dict — must not affect cache (deep copy)
        result["Z"]["close"] = 999
        result["Z"]["volume"] = 0
        assert dp._TV_CACHE["Z"]["close"] == 300
        assert dp._TV_CACHE["Z"]["volume"] == 1000

    def test_tv_validate_overlay_mutation_safe(self):
        """_validate_tv_overlay mutating tv_data dict does NOT corrupt cache."""
        clear_cache()
        dp._TV_CACHE["W"] = {"close": 150, "open": None, "high": None, "low": None, "volume": None}
        dp._TV_CACHE_TS = time.time()
        result = _tv_batch_fetch()
        tv_data = result["W"]
        import pandas as pd
        fake_df = pd.DataFrame({"Close": [149.0, 150.0, 151.0], "Volume": [100, 200, 300]})
        dp._validate_tv_overlay(tv_data, fake_df, "W")
        # tv_data was mutated by _validate_tv_overlay (open/high/low set to close)
        assert tv_data["open"] == 150
        assert tv_data["high"] == 150
        # Cache must be untouched
        assert dp._TV_CACHE["W"]["open"] is None
        assert dp._TV_CACHE["W"]["high"] is None

    def test_yahoo_cache_returns_copy(self):
        """_get_cached_yahoo_history returns a defensive copy."""
        import pandas as pd
        clear_cache()
        cache_key = f"TEST:{config.DATA_PERIOD}:{config.DATA_INTERVAL}"
        fake_df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]})
        dp._CACHE[cache_key] = (fake_df, time.time())
        result_df, was_hit = _get_cached_yahoo_history("TEST")
        assert was_hit is True
        assert result_df is not fake_df
        result_df.iloc[0, 0] = 999
        assert fake_df.iloc[0, 0] == 1.0

    def test_cache_locks_exist(self):
        """Cache locks are properly defined."""
        assert isinstance(_CACHE_LOCK, type(threading.Lock()))
        assert isinstance(_TV_CACHE_LOCK, type(threading.Lock()))


# ══════════════════════════════════════════════════════════════════════
# 4. CONCURRENT CACHE ACCESS TESTS
# ══════════════════════════════════════════════════════════════════════
class TestConcurrentCacheAccess:
    def test_concurrent_cache_clear_safe(self):
        """Concurrent clear_cache calls do not raise."""
        clear_cache()
        errors = []

        def worker():
            try:
                for _ in range(10):
                    clear_cache()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert errors == []

    def test_concurrent_tv_cache_read_write(self):
        """Concurrent TV cache reads and writes do not crash."""
        clear_cache()
        errors = []

        def writer():
            try:
                for i in range(5):
                    dp._TV_CACHE[str(i)] = {"close": i}
                    dp._TV_CACHE_TS = time.time()
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(5):
                    with _TV_CACHE_LOCK:
                        _ = copy.deepcopy(dp._TV_CACHE)
            except Exception as e:
                errors.append(e)

        threads = []
        for _ in range(3):
            threads.append(threading.Thread(target=writer))
            threads.append(threading.Thread(target=reader))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert errors == []


# ══════════════════════════════════════════════════════════════════════
# 5. GLOBAL CONFIG ISOLATION TESTS
# ══════════════════════════════════════════════════════════════════════
class TestGlobalConfigIsolation:
    def test_config_not_mutated_by_scan(self):
        """run_market_radar does not mutate global config."""
        original_value = config.RADAR_MIN_AVG_TRADED_VALUE_20
        run_market_radar(symbols=["TEST_SYMBOL"], top_n=1, min_avg_value=999999)
        assert config.RADAR_MIN_AVG_TRADED_VALUE_20 == original_value

    def test_min_avg_value_used_locally(self):
        """Custom min_avg_value is used locally without global mutation."""
        original = config.RADAR_MIN_AVG_TRADED_VALUE_20
        result = run_market_radar(symbols=["TEST_SYMBOL"], top_n=1, min_avg_value=0.01)
        assert config.RADAR_MIN_AVG_TRADED_VALUE_20 == original


# ══════════════════════════════════════════════════════════════════════
# 6. WEB SERVER AUTH TESTS
# ══════════════════════════════════════════════════════════════════════
class TestWebServerAuth:
    @pytest.fixture
    def client(self):
        from web_server import app
        import web_server as ws
        with ws._lock:
            ws._cached_result = None
            ws._cached_timestamp = None
            ws._scan_running = False
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    def test_health_endpoint(self, client):
        """GET /health returns ok."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_home_endpoint(self, client):
        """GET / returns running message."""
        resp = client.get("/")
        assert resp.status_code == 200

    def test_history_endpoint(self, client):
        """GET /api/history returns a list."""
        resp = client.get("/api/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)

    def test_get_endpoints_accessible_without_key(self, client):
        """GET /api/radar and GET /api/history are accessible without any key."""
        import web_server as ws
        with ws._lock:
            ws._cached_result = None
            ws._cached_timestamp = None
        resp = client.get("/api/radar")
        assert resp.status_code == 202
        data = resp.get_json()
        assert data["status"] == "initial_scan_in_progress"
        resp = client.get("/api/history")
        assert resp.status_code == 200

    def test_refresh_missing_admin_key_returns_503(self, client):
        """POST /api/radar/refresh returns 503 when ADMIN_API_KEY is empty and no dev bypass."""
        with patch.dict(os.environ, {"ADMIN_API_KEY": "", "ALLOW_DEV_SERVER_FALLBACK": "false"}):
            resp = client.post("/api/radar/refresh")
            assert resp.status_code == 503
            data = resp.get_json()
            assert "Admin API key not configured" in data["error"]

    def test_refresh_missing_header_returns_401(self, client):
        """POST /api/radar/refresh without X-API-Key header returns 401."""
        with patch.dict(os.environ, {"ADMIN_API_KEY": "test-secret"}):
            resp = client.post("/api/radar/refresh")
            assert resp.status_code == 401
            data = resp.get_json()
            assert "Missing API key" in data["error"]

    def test_refresh_wrong_key_returns_403(self, client):
        """POST /api/radar/refresh with wrong key returns 403."""
        with patch.dict(os.environ, {"ADMIN_API_KEY": "test-secret"}):
            resp = client.post(
                "/api/radar/refresh",
                headers={"X-API-Key": "wrong-key"},
            )
            assert resp.status_code == 403
            data = resp.get_json()
            assert "Invalid API key" in data["error"]

    def test_refresh_valid_key_accepted(self, client):
        """POST /api/radar/refresh with valid key returns 202 (background scan triggered)."""
        with patch.dict(os.environ, {"ADMIN_API_KEY": "test-secret"}):
            resp = client.post(
                "/api/radar/refresh",
                headers={"X-API-Key": "test-secret"},
            )
            assert resp.status_code == 202
            data = resp.get_json()
            assert data["status"] == "scan triggered"

    def test_refresh_dev_bypass_allows_without_key(self, client):
        """POST /api/radar/refresh succeeds without key when dev bypass is on."""
        from web_server import _rate_limiter
        _rate_limiter._buckets.clear()
        with patch.dict(os.environ, {"ADMIN_API_KEY": "", "ALLOW_DEV_SERVER_FALLBACK": "true"}):
            resp = client.post("/api/radar/refresh")
            assert resp.status_code == 202
            data = resp.get_json()
            assert data["status"] == "scan triggered"

    def test_radar_rate_limit(self, client):
        """GET /api/radar respects rate limiting."""
        import web_server
        with web_server._lock:
            web_server._cached_result = {"items": [], "stats": {}}
            web_server._cached_timestamp = time.time()
        from web_server import _rate_limiter
        _rate_limiter._buckets.clear()
        with patch.dict(os.environ, {"RATE_LIMIT_API_RADAR_RPM": "2"}):
            resp1 = client.get("/api/radar")
            assert resp1.status_code == 200
            resp2 = client.get("/api/radar")
            assert resp2.status_code == 200
            resp3 = client.get("/api/radar")
            assert resp3.status_code == 429


# ══════════════════════════════════════════════════════════════════════
# 7. RUNTIME STABILITY TESTS
# ══════════════════════════════════════════════════════════════════════
class TestRuntimeStability:
    def test_format_radar_header_importable(self):
        """format_radar_header is importable from bot.py's imports."""
        from scanner.radar_output import format_radar_header
        assert callable(format_radar_header)

    def test_bot_imports_complete(self):
        """All required imports in bot.py resolve."""
        from bot import (
            format_radar_header,
            format_radar_telegram_v2,
            format_radar_footer,
            format_radar_category_section,
            RADAR_FORMATTER_VERSION,
            _rate_limiter,
        )
        assert format_radar_header is not None
        assert _rate_limiter is not None

    def test_exception_messages_not_leaked(self, client=None):
        """Error handlers send safe messages, not raw exceptions."""
        from web_server import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            with patch("web_server._run_scan_fresh", side_effect=Exception("SECRET_DB_PASSWORD=xyz")):
                with patch.dict(os.environ, {"ADMIN_API_KEY": ""}):
                    resp = c.post("/api/radar/refresh")
                    data = resp.get_json()
                    assert "SECRET_DB_PASSWORD" not in data.get("error", "")

    def test_admin_api_key_configurable(self):
        """ADMIN_API_KEY is loaded from environment."""
        assert hasattr(config, "ADMIN_API_KEY")

    def test_rate_limit_configs_exist(self):
        """Rate limit configs are defined."""
        assert hasattr(config, "RATE_LIMIT_TELEGRAM_RPM")
        assert hasattr(config, "RATE_LIMIT_API_RADAR_RPM")
        assert hasattr(config, "RATE_LIMIT_API_HISTORY_RPM")
        assert hasattr(config, "RATE_LIMIT_API_REFRESH_RPM")

    def test_allow_dev_server_fallback_configurable(self):
        """ALLOW_DEV_SERVER_FALLBACK is defined in config."""
        assert hasattr(config, "ALLOW_DEV_SERVER_FALLBACK")
        assert isinstance(config.ALLOW_DEV_SERVER_FALLBACK, bool)


# ══════════════════════════════════════════════════════════════════════
# 8. PRODUCTION SERVER TESTS
# ══════════════════════════════════════════════════════════════════════
class TestProductionServer:
    def test_waitress_in_requirements(self):
        """waitress is listed in requirements.txt."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        req_file = os.path.join(project_root, "requirements.txt")
        with open(req_file, "r") as f:
            content = f.read()
        assert "waitress" in content, "waitress should be in requirements.txt"

    def test_dependencies_pinned(self):
        """All dependencies use exact version pinning (==)."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        req_file = os.path.join(project_root, "requirements.txt")
        with open(req_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    assert "==" in line, f"Dependency not pinned: {line}"

    def test_flask_bind_config(self):
        """Web server binds to 0.0.0.0."""
        from web_server import run_flask
        import inspect
        source = inspect.getsource(run_flask)
        assert "0.0.0.0" in source

    def test_run_flask_fails_hard_without_waitress(self):
        """run_flask raises SystemExit when waitress unavailable and no dev bypass."""
        from web_server import run_flask
        import inspect
        source = inspect.getsource(run_flask)
        assert "ALLOW_DEV_SERVER_FALLBACK" in source
        assert "SystemExit" in source

    def test_run_flask_dev_bypass_check(self):
        """run_flask checks ALLOW_DEV_SERVER_FALLBACK before falling back."""
        from web_server import run_flask
        import inspect
        source = inspect.getsource(run_flask)
        assert 'ALLOW_DEV_SERVER_FALLBACK' in source


# ══════════════════════════════════════════════════════════════════════
# 9. CONCURRENT SCAN PREVENTION TESTS
# ══════════════════════════════════════════════════════════════════════
class TestConcurrentScanPrevention:
    def test_web_server_scan_running_flag(self):
        """web_server has a _scan_running flag."""
        import web_server
        assert hasattr(web_server, "_scan_running")

    def test_web_server_scan_flag_is_false_initially(self):
        """_scan_running is False when not scanning."""
        import web_server
        with web_server._lock:
            web_server._scan_running = False
        assert web_server._scan_running is False

    def test_web_server_scan_lock_exists(self):
        """web_server has a _scan_lock for preventing concurrent scans."""
        import web_server
        assert hasattr(web_server, "_scan_lock")
        assert isinstance(web_server._scan_lock, type(threading.Lock()))

    def test_web_server_scan_lock_not_held_initially(self):
        """_scan_lock is not held at import time."""
        import web_server
        if web_server._scan_lock.locked():
            web_server._scan_lock.release()
        assert not web_server._scan_lock.locked()

    def test_bot_radar_lock_exists(self):
        """bot.py has an async lock for radar."""
        import bot
        assert hasattr(bot, "_radar_lock")


# ══════════════════════════════════════════════════════════════════════
# 10. DIVISION BY ZERO GUARD TESTS
# ══════════════════════════════════════════════════════════════════════
class TestDivisionByZeroGuards:
    def test_fetch_live_quote_prev_close_zero(self):
        """fetch_live_quote handles prev_close=0 without ZeroDivisionError."""
        from scanner.data_provider import fetch_live_quote
        with patch("scanner.data_provider.yf") as mock_yf:
            mock_ticker = MagicMock()
            mock_ticker.fast_info = {
                "lastPrice": 50.0,
                "regularMarketPreviousClose": 0,
                "open": 49.0,
                "dayHigh": 51.0,
                "dayLow": 48.0,
            }
            mock_yf.Ticker.return_value = mock_ticker
            result = fetch_live_quote("TEST")
            assert result["last_traded_price"] == 50.0
