"""
Scoring engine — Step 11.
Total = 100 points.

Item                          Score
────────────────────────────  ─────
Above EMA200                  15
EMA Alignment                 10
Trend Quality (Slope Bonus)    5
MACD                          15
RSI                           10
VPVR                          15
Relative Volume               15
Confirmed Breakout            15
"""

from . import config


def calculate_score(result: dict) -> int:
    if result.get("error"):
        return 0

    score = 0

    # Above EMA200 (15 pts)
    if result["trend"]["above_ema200"]:
        score += config.SCORE_EMA200

    # EMA Alignment (10 pts)
    if result["trend"]["ema_aligned"]:
        score += config.SCORE_EMA_ALIGN

    # Trend Quality — Slope Bonus (5 pts)
    if result["trend"]["trend_quality"]:
        score += config.SCORE_TREND_QUALITY

    # MACD (15 pts)
    if result["macd_bullish"]:
        score += config.SCORE_MACD

    # RSI (10 pts — with extended penalty)
    if result["rsi_pass"]:
        rsi_score = config.SCORE_RSI
        if result["rsi_extended"]:
            rsi_score -= config.RSI_EXTENDED_PENALTY
        score += max(0, rsi_score)

    # VPVR (15 pts)
    score += result["vp"]["volume_profile_score"]

    # Relative Volume (15 pts)
    if result["rvol_pass"]:
        if result["rvol"] >= 2.0:
            score += config.SCORE_RVOL
        elif result["rvol"] >= 1.5:
            score += int(config.SCORE_RVOL * 0.8)
        else:
            score += int(config.SCORE_RVOL * 0.6)

    # Confirmed Breakout (15 pts)
    if result["breakout_confirmed"]:
        score += config.SCORE_BREAKOUT

    return min(100, max(0, score))
