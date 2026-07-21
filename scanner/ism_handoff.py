"""
ISM Handoff — Neutral payload for passing radar findings to ISM.

Lite must not send a pre-decided bullish or bearish recommendation to ISM.
ISM must perform its own independent full analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import config


@dataclass
class ISMHandoff:
    """Neutral handoff payload for ISM deep analysis."""
    symbol: str
    requested_by: str = "LITE_MARKET_RADAR"
    activity_category: str = ""
    activity_score: int = 0
    activity_level: str = ""
    radar_reasons: list[str] = field(default_factory=list)
    price_date: str = ""
    data_mode: str = config.DATA_MODE_DAILY

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "requested_by": self.requested_by,
            "activity_category": self.activity_category,
            "activity_score": self.activity_score,
            "activity_level": self.activity_level,
            "radar_reasons": self.radar_reasons,
            "price_date": self.price_date,
            "data_mode": self.data_mode,
        }

    def to_command_text(self) -> str:
        """Format as a ready-to-consume command for ISM."""
        return (
            f"ISM ANALYSIS REQUEST\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Symbol        : {self.symbol}\n"
            f"Requested by  : {self.requested_by}\n"
            f"Activity      : {self.activity_category}\n"
            f"Score         : {self.activity_score}/100\n"
            f"Level         : {self.activity_level}\n"
            f"Price Date    : {self.price_date}\n"
            f"Data Mode     : {self.data_mode}\n\n"
            f"Radar Reasons:\n"
            + "\n".join(f"  • {r}" for r in self.radar_reasons)
            + "\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Run ISM full analysis for {self.symbol}"
        )


def create_handoff(item) -> ISMHandoff:
    """
    Create an ISM handoff from a RadarItem.

    Parameters
    ----------
    item : RadarItem
        A radar analysis result.

    Returns
    -------
    ISMHandoff
    """
    return ISMHandoff(
        symbol=item.symbol,
        requested_by="LITE_MARKET_RADAR",
        activity_category=item.activity_category,
        activity_score=item.activity_score,
        activity_level=item.activity_level,
        radar_reasons=list(item.reasons),
        price_date=item.price_date,
        data_mode=item.data_mode,
    )
