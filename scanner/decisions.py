"""
Decision Engine — Step 12.
"""

from . import config

DECISION_EMOJI = {
    "READY": "🟢",
    "WATCH": "🟡",
    "MONITOR": "🟠",
    "IGNORE": "🔴",
}


def classify(score: int) -> str:
    if score >= config.DECISION_READY:
        return "READY"
    elif score >= config.DECISION_WATCH:
        return "WATCH"
    elif score >= config.DECISION_MONITOR:
        return "MONITOR"
    else:
        return "IGNORE"
