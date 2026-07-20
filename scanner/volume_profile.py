import numpy as np
import pandas as pd
from . import config


def build_volume_profile(data: pd.DataFrame) -> dict:
    """Build VPVR and return POC, distance from POC, and score."""
    high = data["High"].astype(float).values
    low = data["Low"].astype(float).values
    close = data["Close"].astype(float).values
    volume = data["Volume"].astype(float).values
    n = len(high)

    price_min = float(np.min(low))
    price_max = float(np.max(high))
    if price_max <= price_min:
        price_max = price_min + 1.0

    num_bins = config.VPVR_BINS
    bin_width = (price_max - price_min) / num_bins
    bin_edges = np.linspace(price_min, price_max, num_bins + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

    vol_profile = np.zeros(num_bins, dtype=np.float64)
    for i in range(n):
        bar_low = float(low[i])
        bar_high = float(high[i])
        bar_vol = float(volume[i])
        start_bin = int(max(0, (bar_low - price_min) / bin_width))
        end_bin = int(min(num_bins - 1, (bar_high - price_min) / bin_width))
        if start_bin > end_bin:
            start_bin, end_bin = end_bin, start_bin
        num_touched = end_bin - start_bin + 1
        vol_profile[start_bin:end_bin + 1] += bar_vol / num_touched

    total_vol = float(np.sum(vol_profile))
    if total_vol <= 0:
        return {"poc": 0, "above_poc": False, "distance_from_poc_pct": 0, "volume_profile_score": 0}

    poc_idx = int(np.argmax(vol_profile))
    poc = round(float(bin_centers[poc_idx]), 2)

    current_close = float(close[-1])
    above_poc = current_close > poc
    distance_from_poc_pct = round(((current_close - poc) / poc) * 100, 2) if poc > 0 else 0

    # Score based on distance from POC (closer = better, above = better)
    if above_poc:
        if abs(distance_from_poc_pct) < 3:
            score = 15
        elif abs(distance_from_poc_pct) < 7:
            score = 12
        elif abs(distance_from_poc_pct) < 15:
            score = 8
        else:
            score = 4
    else:
        score = max(0, 5 + int(distance_from_poc_pct))

    return {
        "poc": poc,
        "above_poc": above_poc,
        "distance_from_poc_pct": distance_from_poc_pct,
        "volume_profile_score": min(15, max(0, score)),
    }
