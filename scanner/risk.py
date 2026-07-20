"""
Risk — Step 10.
ATR Risk information. Display only, NOT used in scoring.
"""


def format_risk(result: dict) -> dict:
    if result.get("error"):
        return {"atr": 0, "stop_loss": 0, "risk_pct": 0}
    return {
        "atr": result["atr"],
        "stop_loss": result["suggested_stop"],
        "risk_pct": result["risk_pct"],
    }
