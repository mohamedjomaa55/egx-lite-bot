from __future__ import annotations

import pandas as pd


def calculate_ema(series: pd.Series, span: int) -> pd.Series:
    """Return an EMA series with the requested span."""
    return series.ewm(span=span, adjust=False).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate the RSI indicator using Wilder's smoothing."""
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)

    avg_gain = gains.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def calculate_macd(series: pd.Series) -> pd.DataFrame:
    """Calculate MACD line, signal line, and histogram."""
    ema_fast = calculate_ema(series, span=12)
    ema_slow = calculate_ema(series, span=26)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line

    return pd.DataFrame(
        {
            "MACD": macd_line,
            "Signal": signal_line,
            "Histogram": histogram,
        }
    )


def calculate_vpvr_poc(df: pd.DataFrame, price_bins: int = 20) -> float:
    """Approximate the VPVR Point of Control from a simplified volume profile."""
    if df.empty:
        return float("nan")

    price_range = df["Close"].max() - df["Close"].min()
    if price_range == 0:
        return float(df["Close"].iloc[-1])

    bins = pd.cut(df["Close"], bins=price_bins, include_lowest=True)
    profile = df.groupby(bins)["Volume"].sum().sort_values(ascending=False)
    if profile.empty:
        return float(df["Close"].iloc[-1])

    dominant_bin = profile.index[0]
    return float(dominant_bin.mid)
