from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ScoreResult:
    """Holds the scanner ranking result for one ticker."""

    ticker: str
    name: str
    close: float
    trend: int
    macd: int
    rsi: int
    volume: int
    vpvr: int
    breakout: int
    score: int
    warning: str = ""


def candidate_rating(score: int) -> tuple[str, str]:
    """Translate a numeric score into a star rating and candidate label."""
    if score >= 90:
        return "★★★★★", "Strong Candidate"
    if score >= 80:
        return "★★★★☆", "Good Candidate"
    if score >= 70:
        return "★★★☆☆", "Watchlist"
    return "", "Ignore"


def build_score_result(
    ticker: str,
    name: str,
    close: float,
    *,
    trend_score: int,
    macd_score: int,
    rsi_score: int,
    volume_score: int,
    vpvr_score: int,
    breakout_score: int,
    warning: str = "",
) -> ScoreResult:
    """Create a scorer output object with total score."""
    total = trend_score + macd_score + rsi_score + volume_score + vpvr_score + breakout_score
    return ScoreResult(
        ticker=ticker,
        name=name,
        close=close,
        trend=trend_score,
        macd=macd_score,
        rsi=rsi_score,
        volume=volume_score,
        vpvr=vpvr_score,
        breakout=breakout_score,
        score=total,
        warning=warning,
    )
