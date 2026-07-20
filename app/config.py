from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class ScanConfig(BaseModel):
    """Centralized configuration for the EGX Swing Scout scanner."""

    tickers: list[str] = Field(
        default_factory=lambda: [
            "ISPH.CA",
            "AMOC.CA",
            "ICFC.CA",
            "IFAP.CA",
            "OCDI.CA",
            "RMDA.CA",
            "ACGC.CA",
            "ARCC.CA",
            "CIRA.CA",
            "ETRS.CA",
            "ETEL.CA",
            "MPCO.CA",
            "ORWE.CA",
            "MTIE.CA",
            "ORAS.CA",
            "ORHD.CA",
            "EFIH.CA",
            "EFID.CA",
            "PHDC.CA",
            "SAUD.CA",
            "FAITA.CA",
            "FAIT.CA",
            "JUFO.CA",
            "RACC.CA",
            "SKPC.CA",
            "OLFI.CA",
            "EGAS.CA",
            "LCSW.CA",
            "TMGH.CA",
            "MASR.CA",
            "ATQA.CA",
            "MCQE.CA",
            "EGAL.CA",
            "ADIB.CA",
        ]
    )
    report_dir: Path = Path("reports")
    chart_dir: Path = Path("charts")
    chart_top_n: int = 5
    liquidity_threshold: float = 2_000_000.0
    volume_lookback: int = 10
    breakout_lookback: int = 20
    min_rsi: float = 55.0
    max_rsi: float = 75.0
    trend_ema_fast: int = 50
    trend_ema_slow: int = 200
    provider_name: str = "yahoo"
    score_weights: dict[str, int] = Field(
        default_factory=lambda: {
            "trend": 25,
            "macd": 15,
            "rsi": 10,
            "volume": 15,
            "vpvr": 15,
            "breakout": 20,
        }
    )


DEFAULT_CONFIG = ScanConfig()
