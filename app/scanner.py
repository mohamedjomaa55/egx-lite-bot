from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from app.config import DEFAULT_CONFIG
from app.filters import apply_filters
from app.indicators import calculate_ema, calculate_macd, calculate_rsi, calculate_vpvr_poc
from app.scoring import build_score_result, candidate_rating

logger = logging.getLogger(__name__)


class Scanner:
    """Main market scanner for daily EGX swing stock ranking."""

    def __init__(self, config=DEFAULT_CONFIG) -> None:
        self.config = config
        self.report_dir = Path(self.config.report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def _load_data(self, ticker: str) -> pd.DataFrame:
        """Load market data for the ticker using the configured data provider."""
        try:
            import yfinance as yf

            ticker_obj = yf.Ticker(ticker)
            data = ticker_obj.history(period="1y", interval="1d", auto_adjust=False)
            if data.empty:
                raise ValueError(f"No data returned for {ticker}")
            if "Date" not in data.columns:
                data = data.reset_index()
            return data
        except Exception as exc:
            logger.exception("Unable to load data for %s", ticker)
            raise RuntimeError(f"Failed to load data for {ticker}: {exc}") from exc

    def scan(self) -> list[dict[str, object]]:
        """Run the scanner and return ranked candidates."""
        results: list[dict[str, object]] = []

        for ticker in self.config.tickers:
            try:
                df = self._load_data(ticker)
                if df.empty:
                    continue

                df = self._prepare_df(df)
                ema50 = calculate_ema(df["Close"], self.config.trend_ema_fast)
                ema200 = calculate_ema(df["Close"], self.config.trend_ema_slow)
                macd = calculate_macd(df["Close"])
                rsi = calculate_rsi(df["Close"])

                volume_avg = df["Volume"].tail(self.config.volume_lookback).mean()
                price_poc = calculate_vpvr_poc(df)
                breakout_series = df["High"].shift(1).rolling(self.config.breakout_lookback).max()
                breakout = bool(float(df["Close"].iloc[-1]) > float(breakout_series.iloc[-1]))

                filter_result = apply_filters(
                    df,
                    ema50=ema50,
                    ema200=ema200,
                    macd=macd,
                    rsi=rsi,
                    volume_avg=volume_avg,
                    liquidity_threshold=self.config.liquidity_threshold,
                    price_poc=price_poc,
                    breakout=breakout,
                    min_rsi=self.config.min_rsi,
                    max_rsi=self.config.max_rsi,
                )

                if not filter_result.passed:
                    continue

                close = float(df["Close"].iloc[-1])
                rsi_value = float(rsi.iloc[-1])
                trend_score = 25 if float(ema50.iloc[-1]) > float(ema200.iloc[-1]) else 0
                macd_score = 15 if float(macd["MACD"].iloc[-1]) > float(macd["Signal"].iloc[-1]) else 0
                rsi_score = 10 if self.config.min_rsi <= rsi_value <= self.config.max_rsi else 5
                volume_score = 15 if float(df["Volume"].iloc[-1]) > volume_avg else 0
                vpvr_score = 15 if close > float(price_poc) else 0
                breakout_score = 20 if breakout else 0

                score_result = build_score_result(
                    ticker,
                    ticker,
                    close,
                    trend_score=trend_score,
                    macd_score=macd_score,
                    rsi_score=rsi_score,
                    volume_score=volume_score,
                    vpvr_score=vpvr_score,
                    breakout_score=breakout_score,
                    warning=filter_result.warning,
                )
                rating, rating_label = candidate_rating(score_result.score)

                results.append(
                    {
                        "Ticker": ticker,
                        "Name": ticker,
                        "Close": round(close, 2),
                        "Score": score_result.score,
                        "Trend": score_result.trend,
                        "MACD": score_result.macd,
                        "RSI": round(rsi_value, 2),
                        "Volume": int(df["Volume"].iloc[-1]),
                        "Breakout": breakout_score,
                        "Warning": filter_result.warning,
                        "Rating": rating,
                        "RatingLabel": rating_label,
                    }
                )
            except Exception as exc:
                logger.exception("Error scanning %s", ticker)
                results.append({"Ticker": ticker, "Error": str(exc)})

        results.sort(key=lambda item: item.get("Score", -1), reverse=True)
        return results

    def _prepare_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize the fetched OHLCV data so all downstream code can rely on consistent columns."""
        normalized = df.copy()

        if "Date" not in normalized.columns and normalized.index.name is not None:
            normalized = normalized.reset_index()
            normalized = normalized.rename(columns={normalized.columns[0]: "Date"})
        elif "Date" not in normalized.columns and isinstance(normalized.index, pd.DatetimeIndex):
            normalized = normalized.reset_index()
            normalized = normalized.rename(columns={normalized.columns[0]: "Date"})

        if "Date" in normalized.columns:
            normalized["Date"] = pd.to_datetime(normalized["Date"])

        required = {"Open", "High", "Low", "Close", "Volume"}
        missing = required.difference(normalized.columns)
        if missing:
            raise ValueError(f"Missing required columns: {sorted(missing)}")

        normalized = normalized.sort_values("Date")
        normalized = normalized.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
        normalized["Volume"] = normalized["Volume"].astype(float)
        return normalized
