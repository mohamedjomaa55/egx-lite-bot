"""
Console Output — Steps 14–16.
Uses rich for formatted tables.
"""

from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich import box

from . import config
from .decisions import DECISION_EMOJI


console = Console()


def print_report_header(market_status: str, stats: dict,
                        ready: int, watch: int, monitor: int, ignore: int):
    """Step 16 — Report Header."""
    ms_style = {
        "BULLISH": "bold green",
        "NEUTRAL": "bold yellow",
        "BEARISH": "bold red",
    }.get(market_status, "bold")

    lines = [
        "=" * 40,
        "",
        "    EGX SWING SCOUT v1.0",
        "",
        f"  Market Status   : [{ms_style}]{market_status}[/]",
        f"  Stocks Requested: {stats['stocks_requested']}",
        f"  Stocks Analyzed : {stats['stocks_analyzed']}",
        f"  Data Failures   : {stats['data_failures']}",
        f"  Passed Filters  : {stats['passed_filters']}",
        "",
        f"  READY  : {ready}",
        f"  WATCH  : {watch}",
        f"  MONITOR: {monitor}",
        f"  IGNORE : {ignore}",
        "",
        "=" * 40,
    ]
    console.print("\n".join(lines))


def print_market_warning(warning: str):
    if warning:
        console.print(f"\n  Warning: {warning}\n", style="bold yellow")


def print_results_table(results: list[dict], top_n: int = 10):
    """Step 14 — Compact 9-column table, Top N only."""
    if not results:
        console.print("\n  No actionable candidates found.\n", style="dim")
        return

    display = results[:top_n]

    table = Table(box=box.SIMPLE_HEAVY, show_lines=True, padding=(0, 1))
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Ticker", style="bold cyan", width=8)
    table.add_column("Name", width=12)
    table.add_column("Close", justify="right", width=8)
    table.add_column("Score", justify="right", width=5, style="bold")
    table.add_column("Decision", width=10)
    table.add_column("RSI", justify="right", width=5)
    table.add_column("RVOL", justify="right", width=5)
    table.add_column("Next Action", width=14)

    for i, r in enumerate(display, 1):
        emoji = DECISION_EMOJI.get(r["decision"], "")
        decision_str = f"{emoji} {r['decision']}"

        name = config.STOCK_NAMES.get(r["ticker"], "")
        if len(name) > 12:
            name = name[:10] + ".."

        dec_style = {"READY": "bold green", "WATCH": "bold yellow",
                     "MONITOR": "dark_orange", "IGNORE": "red"}.get(r["decision"], "")

        table.add_row(
            str(i),
            r["ticker"],
            name,
            f"{r['close']:.2f}",
            f"{r['score']}",
            Text(decision_str, style=dec_style),
            f"{r['rsi']:.0f}",
            f"{r['rvol']:.2f}",
            r["next_action"],
        )

    console.print(table)


def print_detail_table(results: list[dict]):
    """Detailed per-stock view with reasons."""
    for i, r in enumerate(results, 1):
        emoji = DECISION_EMOJI.get(r["decision"], "")
        dec_style = {"READY": "bold green", "WATCH": "bold yellow",
                     "MONITOR": "dark_orange", "IGNORE": "red"}.get(r["decision"], "")

        console.print(f"\n  #{i}  {r['ticker']}  —  {r['score']}/100  {emoji} {r['decision']}", style=dec_style)
        console.print(f"  Close: {r['close']}  ATR: {r['atr']}  Stop: {r['suggested_stop']}  Risk: {r['risk_pct']}%")
        console.print(f"  EMA20: {r['ema20']}  EMA50: {r['ema50']}  EMA200: {r['ema200']}")
        console.print(f"  RSI: {r['rsi']}  MACD: {r['macd_hist']}  RVOL: {r['rvol']}")
        console.print(f"  POC: {r['vp']['poc']} ({r['vp']['distance_from_poc_pct']}%)  Res: {r['resistance']} ({r['resistance_dist_pct']}%)")
        for reason in r.get("reasons", []):
            style = "green" if reason.startswith("✓") else "red" if reason.startswith("✗") else "yellow"
            console.print(f"    {reason}", style=style)


def print_failed_tickers(failed: list[dict]):
    if not failed:
        return
    console.print(f"\n  Data failures ({len(failed)}):", style="bold red")
    for f in failed:
        console.print(f"    {f['ticker']} — {f['reason']}", style="dim red")


def print_footer():
    console.print("\n" + "=" * 40, style="bold")
    console.print("  NOT a Buy/Sell recommendation.", style="dim")
    console.print("  Priority ranking to help you focus.", style="dim")
    console.print("=" * 40, style="bold")
