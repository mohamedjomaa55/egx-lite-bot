"""
EGX Swing Scout v1.0 — Entry Point

A post-market scanner for the Egyptian Exchange (EGX).
It filters, ranks, and explains — it NEVER generates Buy/Sell signals.

Usage:
    python main.py                    Scan all EGX stocks
    python main.py -t COMI -t ORAS    Scan specific tickers
"""

import sys
import os
import logging

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import typer

app = typer.Typer(help="EGX Swing Scout v1.0 — Post-market opportunity scanner.")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%H:%M:%S",
)


@app.command()
def scan(
    tickers: list[str] = typer.Option(None, "--tickers", "-t", help="Specific tickers to scan"),
    export: bool = typer.Option(True, "--export/--no-export", help="Export CSV"),
    detail: bool = typer.Option(False, "--detail", "-d", help="Show detailed per-stock output"),
):
    """Run the EGX Swing Scout scanner."""
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


if __name__ == "__main__":
    app()
