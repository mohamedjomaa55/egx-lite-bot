"""
EGX Swing Scout v1.0 — Configuration
Every tunable parameter in one place.
"""

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

# ── Future Placeholders (NOT implemented v1.0) ────────────────────────
# Telegram, Charts, Dashboard, Backtesting
# Wyckoff, Fibonacci, Elliott, AI Ranking, Pattern Recognition
