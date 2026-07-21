"""
EGX Lite Market Radar v2.0 — Entry Point

A post-market activity scanner for the Egyptian Exchange (EGX).
Detects unusual volume activity, buying/selling pressure, and inconclusive signals.
It NEVER generates Buy/Sell signals — those decisions belong to ISM.

Usage:
    python main.py                        Full radar scan
    python main.py radar                  Radar scan (default top 20)
    python main.py radar --top 10         Radar scan, top 10
    python main.py radar -t ARCC -t COMI  Radar scan specific tickers
    python main.py scan                   Legacy scan (deprecated)
"""

import sys
import os
import logging

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import typer

app = typer.Typer(help="EGX Lite Market Radar v2.0 — Post-market activity scanner.")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%H:%M:%S",
)


@app.command()
def radar(
    tickers: list[str] = typer.Option(None, "--tickers", "-t", help="Specific tickers to scan"),
    top: int = typer.Option(20, "--top", "-n", help="Number of top results"),
    include_normal: bool = typer.Option(False, "--include-normal", help="Include NORMAL activity stocks"),
    detail: bool = typer.Option(False, "--detail", "-d", help="Show detailed per-stock output"),
):
    """Run the EGX Lite Market Radar scan."""
    from scanner.market_radar import run_market_radar
    from scanner.radar_output import (
        print_radar_header,
        print_radar_table,
        print_radar_failed,
        print_radar_footer,
    )
    from scanner import config

    if include_normal:
        config.RADAR_INCLUDE_NORMAL = True

    result = run_market_radar(symbols=tickers, top_n=top)

    print_radar_header(result)
    print_radar_table(result)
    print_radar_failed(result)
    print_radar_footer()

    if detail and result.items:
        for item in result.items:
            print(f"\n--- {item.symbol} ({item.company_name}) ---")
            print(f"  Price: {item.price:.2f} ({item.price_change_percent:+.1f}%)")
            print(f"  Volume: {item.volume} / Avg: {item.average_volume_20:.0f} / RVOL: {item.rvol_20:.2f}")
            print(f"  Score: {item.activity_score}/100 = {item.activity_score_components}")
            print(f"  Category: {item.activity_category} — {item.activity_label}")
            print(f"  Level: {item.activity_level}")
            print(f"  RSI: {item.rsi_14:.1f} (prev: {item.rsi_previous:.1f}, change: {item.rsi_change:+.1f})")
            print(f"  MACD Hist: {item.macd_histogram:.4f} (change: {item.macd_histogram_change:+.4f})")
            print(f"  Close Location: {item.close_location_value:.3f}")
            print(f"  Body %: {item.candle_body_percent:.1f}%")
            print(f"  Vol Percentile(60): {item.volume_percentile_60:.1f}%")
            if item.adx_14 is not None:
                print(f"  ADX: {item.adx_14:.1f}")
            if item.price_return_5d is not None:
                print(f"  5d Return: {item.price_return_5d:+.1f}%")
            if item.price_return_20d is not None:
                print(f"  20d Return: {item.price_return_20d:+.1f}%")
            print(f"  Reasons:")
            for r in item.reasons:
                print(f"    • {r}")


@app.command()
def scan(
    tickers: list[str] = typer.Option(None, "--tickers", "-t", help="Specific tickers to scan"),
    export: bool = typer.Option(True, "--export/--no-export", help="Export CSV"),
    detail: bool = typer.Option(False, "--detail", "-d", help="Show detailed per-stock output"),
):
    """[DEPRECATED] Run the legacy EGX Swing Scout scanner."""
    from scanner.scanner import scan as run_scan
    from scanner.console_output import (
        print_report_header,
        print_market_warning,
        print_results_table,
        print_detail_table,
        print_failed_tickers,
        print_footer,
    )
    from scanner.csv_export import export_csv, export_failed_tickers

    typer.echo("⚠️  Legacy scan is deprecated. Use 'python main.py radar' instead.\n")

    results, mkt, stats, elapsed = run_scan(tickers=tickers)

    ready = sum(1 for r in results if r["decision"] == "READY")
    watch = sum(1 for r in results if r["decision"] == "WATCH")
    monitor = sum(1 for r in results if r["decision"] == "MONITOR")
    ignore = sum(1 for r in results if r["decision"] == "IGNORE")

    print_report_header(
        market_status=mkt["status"],
        stats=stats.to_dict(),
        ready=ready,
        watch=watch,
        monitor=monitor,
        ignore=ignore,
    )

    print_market_warning(mkt.get("warning", ""))

    print_results_table(results)

    print_failed_tickers(stats.failed_tickers)

    if detail:
        actionable = [r for r in results if r["decision"] in ("READY", "WATCH", "MONITOR")]
        print_detail_table(actionable)

    if export and results:
        filepath = export_csv(results, market_status=mkt["status"])
        typer.echo(f"\n  CSV exported: {filepath}")

    if export and stats.failed_tickers:
        fpath = export_failed_tickers(stats.failed_tickers)
        typer.echo(f"  Failed tickers exported: {fpath}")

    print_footer()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """EGX Lite Market Radar v2.0 — Default runs radar scan."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(radar)


if __name__ == "__main__":
    app()
