from __future__ import annotations

"""Backtesting placeholder for EGX Swing Scout.

This module is intentionally scoped as a future-ready architecture lander.
It is not used for live buy/sell decision generation.
"""

from dataclasses import dataclass


@dataclass(slots=True)
class BacktestMetrics:
    """Container for the required backtest summary metrics."""

    win_rate: float = 0.0
    average_gain: float = 0.0
    average_loss: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    average_holding_days: float = 0.0


def run_backtest() -> BacktestMetrics:
    """Placeholder that will later run a six-month historical evaluation.

    The design intentionally keeps the interface stable while leaving the actual
    implementation for future iterations.
    """
    return BacktestMetrics()
