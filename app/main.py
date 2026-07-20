from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.config import DEFAULT_CONFIG
from app.report import ReportExporter
from app.scanner import Scanner

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
console = Console()
app = typer.Typer(help="EGX Swing Scout v1.0", no_args_is_help=True)


@app.command()
def run() -> None:
    """Run the daily EGX swing scan and export the result files."""
    scanner = Scanner(DEFAULT_CONFIG)
    results = scanner.scan()

    exporter = ReportExporter(Path(DEFAULT_CONFIG.report_dir))
    exporter.export(results)

    table = Table(title="EGX Swing Scout v1.0 Results")
    table.add_column("Ticker")
    table.add_column("Name")
    table.add_column("Close")
    table.add_column("Score")
    table.add_column("Trend")
    table.add_column("MACD")
    table.add_column("RSI")
    table.add_column("Volume")
    table.add_column("Breakout")
    table.add_column("Warning")

    for row in results:
        if row.get("Error"):
            continue
        table.add_row(
            str(row.get("Ticker", "")),
            str(row.get("Name", "")),
            str(row.get("Close", "")),
            str(row.get("Score", "")),
            str(row.get("Trend", "")),
            str(row.get("MACD", "")),
            str(row.get("RSI", "")),
            str(row.get("Volume", "")),
            str(row.get("Breakout", "")),
            str(row.get("Warning", "")),
        )

    console.print(table)
    typer.echo(f"Scanned {len(results)} candidates and exported reports.")


@app.command()
def show() -> None:
    """Placeholder command for future interactive display logic."""
    typer.echo("EGX Swing Scout v1.0 - placeholder for future UI layer")


if __name__ == "__main__":
    app()
