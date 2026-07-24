"""
EGX Swing Scout v1.0 — Configuration
Every tunable parameter in one place.
"""

import os
import logging

# ── Data Provider Mode ────────────────────────────────────────────────
# fallback  → existing Yahoo Finance provider only
# egxapi    → EGXAPI provider only (requires EGXAPI_KEY)
# shadow    → existing provider primary, EGXAPI validates in background
DATA_PROVIDER = os.getenv("DATA_PROVIDER", "shadow").lower()

# Shadow comparison thresholds
SHADOW_PRICE_MATCH_THRESHOLD = 0.25   # percent: <= this = MATCH
SHADOW_STALE_THRESHOLD_SEC = 900      # 15 minutes in seconds

# ── EGX Universe ──────────────────────────────────────────────────────
EGX_INDEX = "^EGX30"

# Shariah-compliant stocks only
EGX_SYMBOL_MAP = {
    "ISPH": "ISPH.CA", "AMOC": "AMOC.CA", "ICFC": "ICFC.CA",
    "IFAP": "IFAP.CA", "OCDI": "OCDI.CA", "RMDA": "RMDA.CA",
    "ACGC": "ACGC.CA", "ARCC": "ARCC.CA", "CIRA": "CIRA.CA",
    "ETRS": "ETRS.CA", "ETEL": "ETEL.CA", "MPCO": "MPCO.CA",
    "ORWE": "ORWE.CA", "MTIE": "MTIE.CA", "ORAS": "ORAS.CA",
    "ORHD": "ORHD.CA", "EFIH": "EFIH.CA", "EFID": "EFID.CA",
    "PHDC": "PHDC.CA", "SAUD": "SAUD.CA", "FAITA": "FAITA.CA",
    "FAIT": "FAIT.CA", "JUFO": "JUFO.CA", "RACC": "RACC.CA",
    "SKPC": "SKPC.CA", "OLFI": "OLFI.CA", "EGAS": "EGAS.CA",
    "LCSW": "LCSW.CA", "TMGH": "TMGH.CA", "MASR": "MASR.CA",
    "ATQA": "ATQA.CA", "MCQE": "MCQE.CA", "EGAL": "EGAL.CA",
    "ADIB": "ADIB.CA",
}

STOCK_NAMES = {
    "ISPH": "ابن سينا فارما", "AMOC": "الاسكندرية للزيوت المعدنية",
    "ICFC": "الدولية للأسمدة والكيماويات", "IFAP": "الدوليه للمحاصيل الزراعيه",
    "OCDI": "السادس من اكتوبر-سوديك", "RMDA": "العاشر من رمضان-راميدا",
    "ACGC": "العربية لحليج الأقطان", "ARCC": "العربية للاسمنت",
    "CIRA": "سيرا للتعليم", "ETRS": "ايجيترانس",
    "ETEL": "المصرية للاتصالات", "MPCO": "المنصورة للدواجن",
    "ORWE": "النساجون الشرقيون", "MTIE": "ام.ام جروب",
    "ORAS": "أوراسكوم كونستراكشن", "ORHD": "أوراسكوم للتنمية",
    "EFIH": "اي فاينانس", "EFID": "ايديتا",
    "PHDC": "بالم هيلز", "SAUD": "بنك البركة مصر",
    "FAITA": "فيصل الاسلامي-دولار", "FAIT": "فيصل الاسلامي-جنيه",
    "JUFO": "جهينة", "RACC": "راية",
    "SKPC": "سيدى كرير-سيدبك", "OLFI": "عبور لاند",
    "EGAS": "غاز مصر", "LCSW": "ليسيكو",
    "TMGH": "طلعت مصطفى", "MASR": "مدينة مصر",
    "ATQA": "عتاقة", "MCQE": "مصر للاسمنت-قنا",
    "EGAL": "مصر للألمنيوم", "ADIB": "مصرف أبوظبي الإسلامي",
}

# ── Data Settings ─────────────────────────────────────────────────────
DATA_PERIOD = "1y"
DATA_INTERVAL = "1d"

# ── EMA Settings ──────────────────────────────────────────────────────
EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 200

# ── RSI Settings ──────────────────────────────────────────────────────
RSI_PERIOD = 14
RSI_EXTENDED_THRESHOLD = 75
RSI_EXTENDED_PENALTY = 5

# ── MACD Settings ─────────────────────────────────────────────────────
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# ── ATR Settings ──────────────────────────────────────────────────────
ATR_PERIOD = 14

# ── Breakout Settings ─────────────────────────────────────────────────
BREAKOUT_LOOKBACK = 20

# ── Relative Volume Settings ──────────────────────────────────────────
RVOL_MA_PERIOD = 20
RVOL_MIN_THRESHOLD = 1.2

# ── Liquidity Settings ────────────────────────────────────────────────
MIN_VALUE_TRADED = 5_000_000

# ── VPVR Settings ─────────────────────────────────────────────────────
VPVR_BINS = 25

# ── Resistance Distance Warning ───────────────────────────────────────
RESISTANCE_NEAR_THRESHOLD = 2.0  # percent

# ── Scoring Weights (Total = 100) ────────────────────────────────────
SCORE_EMA200 = 15
SCORE_EMA_ALIGN = 10
SCORE_TREND_QUALITY = 5
SCORE_MACD = 15
SCORE_RSI = 10
SCORE_VPVR = 15
SCORE_RVOL = 15
SCORE_BREAKOUT = 15

# ── Decision Thresholds ───────────────────────────────────────────────
DECISION_READY = 90
DECISION_WATCH = 80
DECISION_MONITOR = 70

# ══════════════════════════════════════════════════════════════════════
# MARKET RADAR CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

RADAR_TOP_N = int(os.getenv("RADAR_TOP_N", "20"))
RADAR_MIN_RVOL = float(os.getenv("RADAR_MIN_RVOL", "1.35"))
RADAR_HIGH_RVOL = float(os.getenv("RADAR_HIGH_RVOL", "2.0"))
RADAR_EXTREME_RVOL = float(os.getenv("RADAR_EXTREME_RVOL", "3.0"))
RADAR_MIN_AVG_TRADED_VALUE_20 = float(os.getenv("RADAR_MIN_AVG_TRADED_VALUE_20", "1_000_000"))
RADAR_MIN_PRICE = float(os.getenv("RADAR_MIN_PRICE", "1.0"))
RADAR_MIN_HISTORY_CANDLES = int(os.getenv("RADAR_MIN_HISTORY_CANDLES", "60"))
RADAR_RSI_LENGTH = int(os.getenv("RADAR_RSI_LENGTH", "14"))
RADAR_VOLUME_AVERAGE_LENGTH = int(os.getenv("RADAR_VOLUME_AVERAGE_LENGTH", "20"))
RADAR_MACD_FAST = int(os.getenv("RADAR_MACD_FAST", "12"))
RADAR_MACD_SLOW = int(os.getenv("RADAR_MACD_SLOW", "26"))
RADAR_MACD_SIGNAL = int(os.getenv("RADAR_MACD_SIGNAL", "9"))
RADAR_INCLUDE_NORMAL = os.getenv("RADAR_INCLUDE_NORMAL", "false").lower() == "true"
RADAR_ENABLE_ADX_CONTEXT = os.getenv("RADAR_ENABLE_ADX_CONTEXT", "true").lower() == "true"

# Activity score weights (total = 100)
RADAR_SCORE_VOLUME = int(os.getenv("RADAR_SCORE_VOLUME", "50"))
RADAR_SCORE_LIQUIDITY = int(os.getenv("RADAR_SCORE_LIQUIDITY", "15"))
RADAR_SCORE_PRICE_VOLUME = int(os.getenv("RADAR_SCORE_PRICE_VOLUME", "15"))
RADAR_SCORE_RSI = int(os.getenv("RADAR_SCORE_RSI", "10"))
RADAR_SCORE_MACD = int(os.getenv("RADAR_SCORE_MACD", "10"))

# Activity level thresholds (volume percentile based)
RADAR_PERCENTILE_EXTREME = float(os.getenv("RADAR_PERCENTILE_EXTREME", "95"))
RADAR_PERCENTILE_HIGH = float(os.getenv("RADAR_PERCENTILE_HIGH", "90"))
RADAR_PERCENTILE_ELEVATED = float(os.getenv("RADAR_PERCENTILE_ELEVATED", "75"))

# ══════════════════════════════════════════════════════════════════════
# EGX SESSION CALENDAR
# ══════════════════════════════════════════════════════════════════════

# EGX trading days: Sunday(6), Monday(0), Tuesday(1), Wednesday(2), Thursday(3)
EGX_TRADING_DAYS = {0, 1, 2, 3, 6}

# EGX session times (Cairo time)
EGX_OPEN_HOUR = 9
EGX_OPEN_MINUTE = 30
EGX_CLOSE_HOUR = 14
EGX_CLOSE_MINUTE = 15

# Safety buffer after close before declaring session complete
EGX_SAFETY_BUFFER_MINUTES = 30

# Freshness status constants
FRESHNESS_CURRENT = "CURRENT"
FRESHNESS_PROVIDER_DELAYED = "PROVIDER_DELAYED"
FRESHNESS_MARKET_OPEN = "MARKET_OPEN"
FRESHNESS_NON_TRADING_DAY = "NON_TRADING_DAY"
FRESHNESS_DATA_UNAVAILABLE = "DATA_UNAVAILABLE"



# ── Data Mode Constants ──────────────────────────────────────────────
DATA_MODE_DAILY = "DAILY_COMPLETED_SESSION"
DATA_MODE_LIVE = "LIVE_SESSION"
DATA_MODE_INTRADAY_60M = "INTRADAY_60M"
DATA_MODE_INTRADAY_15M = "INTRADAY_15M"

# ── Failure States ───────────────────────────────────────────────────
FAILURE_INVALID_SYMBOL = "INVALID_SYMBOL"
FAILURE_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
FAILURE_DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
FAILURE_STALE_DATA = "STALE_DATA"
FAILURE_INVALID_VOLUME = "INVALID_VOLUME"
FAILURE_PROVIDER_ERROR = "PROVIDER_ERROR"

# ── Rate Limiting ─────────────────────────────────────────────────────
RATE_LIMIT_TELEGRAM_RPM = int(os.getenv("RATE_LIMIT_TELEGRAM_RPM", "0"))  # 0 = unlimited
RATE_LIMIT_API_RADAR_RPM = int(os.getenv("RATE_LIMIT_API_RADAR_RPM", "30"))
RATE_LIMIT_API_HISTORY_RPM = int(os.getenv("RATE_LIMIT_API_HISTORY_RPM", "60"))
RATE_LIMIT_API_REFRESH_RPM = int(os.getenv("RATE_LIMIT_API_REFRESH_RPM", "5"))

# ── Admin API ────────────────────────────────────────────────────────
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

# ── Server Fallback ──────────────────────────────────────────────────
# When false (default), startup aborts if waitress is unavailable.
ALLOW_DEV_SERVER_FALLBACK = os.getenv("ALLOW_DEV_SERVER_FALLBACK", "false").lower() == "true"

# ── Background Scan Settings ────────────────────────────────────────
_logger = logging.getLogger(__name__)
try:
    _scan_interval_raw = os.getenv("SCAN_INTERVAL_MINUTES", "30")
    SCAN_INTERVAL_MINUTES = int(_scan_interval_raw)
    if SCAN_INTERVAL_MINUTES <= 0:
        raise ValueError
except (ValueError, TypeError):
    _logger.warning(
        "Invalid SCAN_INTERVAL_MINUTES=%r, falling back to 30",
        os.getenv("SCAN_INTERVAL_MINUTES", ""),
    )
    SCAN_INTERVAL_MINUTES = 30

# ── Future Placeholders (NOT implemented v1.0) ────────────────────────
# Telegram, Charts, Dashboard, Backtesting
# Wyckoff, Fibonacci, Elliott, AI Ranking, Pattern Recognition
