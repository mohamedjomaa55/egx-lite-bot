"""
Radar Output — Console and Telegram formatters for Market Radar.
"""

from __future__ import annotations

from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich import box

from . import config
from .market_radar import (
    MarketRadarResult,
    RadarItem,
    ActivityCategory,
    ActivityLevel,
)

console = Console()

_ACTIVITY_EMOJI = {
    ActivityCategory.BUYING: "\U0001f7e2",
    ActivityCategory.SELLING: "\U0001f534",
    ActivityCategory.UNUSUAL: "\U0001f7e1",
}

_LEVEL_EMOJI = {
    ActivityLevel.EXTREME: "\u26a1",
    ActivityLevel.HIGH: "\U0001f525",
    ActivityLevel.ELEVATED: "\u2b50",
    ActivityLevel.NORMAL: "\u26aa",
}


# ─── Console Output ──────────────────────────────────────────────────
def print_radar_header(result: MarketRadarResult):
    """Print the radar scan header."""
    stats = result.stats
    lines = [
        "=" * 60,
        "",
        "    EGX LITE MARKET RADAR",
        "",
        f"  Data Mode       : {result.data_mode}",
        f"  Data Date       : {result.data_date}",
        f"  Scanned         : {stats.symbols_scanned}",
        f"  Activity Found  : {stats.activity_detected}",
        f"  Buying Activity : {stats.buying_count}",
        f"  Selling Activity: {stats.selling_count}",
        f"  Unusual Activity: {stats.unusual_count}",
        f"  Failed          : {stats.failed_count}",
        f"  Skipped (illiq) : {stats.skipped_illiquid}",
        f"  Duration        : {stats.scan_duration}s",
        "",
        "=" * 60,
    ]
    console.print("\n".join(lines))


def print_radar_table(result: MarketRadarResult):
    """Print the radar results table grouped by category."""
    buying = [i for i in result.items if i.activity_category == ActivityCategory.BUYING]
    selling = [i for i in result.items if i.activity_category == ActivityCategory.SELLING]
    unusual = [i for i in result.items if i.activity_category == ActivityCategory.UNUSUAL]

    for category_label, emoji, items in [
        ("BUYING ACTIVITY", "\U0001f7e2", buying),
        ("SELLING ACTIVITY", "\U0001f534", selling),
        ("UNUSUAL ACTIVITY", "\U0001f7e1", unusual),
    ]:
        if not items:
            continue

        console.print(f"\n  {emoji} {category_label}", style="bold")
        table = Table(box=box.SIMPLE_HEAVY, show_lines=True, padding=(0, 1))
        table.add_column("#", style="dim", width=3, justify="right")
        table.add_column("Ticker", style="bold cyan", width=8)
        table.add_column("Name", width=12)
        table.add_column("Price", justify="right", width=8)
        table.add_column("Chg%", justify="right", width=6)
        table.add_column("Score", justify="right", width=5, style="bold")
        table.add_column("Level", width=9)
        table.add_column("RSI", justify="right", width=5)
        table.add_column("RVOL", justify="right", width=5)

        for i, item in enumerate(items, 1):
            level_style = {
                ActivityLevel.EXTREME: "bold magenta",
                ActivityLevel.HIGH: "bold red",
                ActivityLevel.ELEVATED: "bold yellow",
                ActivityLevel.NORMAL: "dim",
            }.get(item.activity_level, "")

            level_emoji = _LEVEL_EMOJI.get(item.activity_level, "")
            name = item.company_name
            if len(name) > 12:
                name = name[:10] + ".."

            chg_style = "green" if item.price_change_percent > 0 else "red" if item.price_change_percent < 0 else "dim"

            table.add_row(
                str(i),
                item.symbol,
                name,
                f"{item.price:.2f}",
                f"{item.price_change_percent:+.1f}%",
                f"{item.activity_score}",
                f"{level_emoji} {item.activity_level}",
                f"{item.rsi_14:.0f}",
                f"{item.rvol_20:.2f}",
            )

        console.print(table)

    # Print reasons for top items
    for i, item in enumerate(result.items[:5], 1):
        console.print(f"\n  #{i} {item.symbol} — {item.activity_label}", style="bold")
        console.print(f"  Price: {item.price:.2f} ({item.price_change_percent:+.1f}%)  Volume: {item.rvol_20:.1f}x  RSI: {item.rsi_14:.0f}")
        for reason in item.reasons:
            console.print(f"    • {reason}", style="dim")


def print_radar_failed(result: MarketRadarResult):
    """Print failed tickers."""
    if not result.stats.failed_tickers:
        return
    console.print(
        f"\n  Data failures ({result.stats.failed_count}):", style="bold red"
    )
    for f in result.stats.failed_tickers:
        console.print(f"    {f['ticker']} — {f['reason']}", style="dim red")


def print_radar_footer():
    """Print the radar footer."""
    console.print("\n" + "=" * 60, style="bold")
    console.print("  Lite detects activity only.", style="dim")
    console.print("  Use ISM for complete technical analysis and trade decisions.", style="dim")
    console.print("=" * 60, style="bold")


# ─── Telegram Output ─────────────────────────────────────────────────
def format_radar_telegram(result: MarketRadarResult, top_n: int | None = None) -> str:
    """
    Format the radar result as a Telegram message.

    Returns the full text as a single string.
    """
    if top_n is None:
        top_n = config.RADAR_TOP_N

    stats = result.stats
    items = result.items[:top_n]

    lines = [
        "\U0001f4e1 EGX LITE MARKET RADAR",
        f"Data: Latest completed session",
        f"Date: {result.data_date}",
        f"Scanned: {stats.symbols_scanned}",
        f"Activity detected: {stats.activity_detected}",
        "",
    ]

    # ── Buying Activity ───────────────────────────────────────────────
    buying = [i for i in items if i.activity_category == ActivityCategory.BUYING]
    if buying:
        lines.append("\U0001f7e2 BUYING ACTIVITY")
        lines.append("")
        for i, item in enumerate(buying, 1):
            rsi_arrow = "\u2191" if item.rsi_change > 0 else "\u2193" if item.rsi_change < 0 else "\u2192"
            lines.append(f"{i}. {item.symbol} \u2014 Activity {item.activity_score}/100")
            lines.append(f"Price: {item.price:.2f} ({item.price_change_percent:+.1f}%)")
            lines.append(f"Volume: {item.rvol_20:.1f}x average")
            lines.append(f"RSI: {item.rsi_14:.0f} {rsi_arrow}")
            hist_arrow = "Improving" if item.macd_histogram_change > 0 else "Weakening"
            lines.append(f"MACD Histogram: {hist_arrow}")
            lines.append(f"Label: {item.activity_label}")
            if item.reasons:
                lines.append("Reasons:")
                for r in item.reasons[:3]:
                    lines.append(f"\u2022 {r}")
            lines.append("")

    # ── Selling Activity ──────────────────────────────────────────────
    selling = [i for i in items if i.activity_category == ActivityCategory.SELLING]
    if selling:
        lines.append("\U0001f534 SELLING ACTIVITY")
        lines.append("")
        for i, item in enumerate(selling, 1):
            rsi_arrow = "\u2191" if item.rsi_change > 0 else "\u2193" if item.rsi_change < 0 else "\u2192"
            lines.append(f"{i}. {item.symbol} \u2014 Activity {item.activity_score}/100")
            lines.append(f"Price: {item.price:.2f} ({item.price_change_percent:+.1f}%)")
            lines.append(f"Volume: {item.rvol_20:.1f}x average")
            lines.append(f"RSI: {item.rsi_14:.0f} {rsi_arrow}")
            hist_arrow = "Improving" if item.macd_histogram_change > 0 else "Weakening"
            lines.append(f"MACD Histogram: {hist_arrow}")
            lines.append(f"Label: {item.activity_label}")
            if item.reasons:
                lines.append("Reasons:")
                for r in item.reasons[:3]:
                    lines.append(f"\u2022 {r}")
            lines.append("")

    # ── Unusual Activity ──────────────────────────────────────────────
    unusual = [i for i in items if i.activity_category == ActivityCategory.UNUSUAL]
    if unusual:
        lines.append("\U0001f7e1 UNUSUAL ACTIVITY")
        lines.append("")
        for i, item in enumerate(unusual, 1):
            lines.append(f"{i}. {item.symbol} \u2014 Activity {item.activity_score}/100")
            lines.append(f"Price: {item.price:.2f} ({item.price_change_percent:+.1f}%)")
            lines.append(f"Volume: {item.rvol_20:.1f}x average")
            lines.append(f"Label: {item.activity_label}")
            if item.reasons:
                lines.append("Reasons:")
                for r in item.reasons[:3]:
                    lines.append(f"\u2022 {r}")
            lines.append("")

    if not items:
        lines.append("No significant activity detected in this scan.")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("Lite detects activity only.")
    lines.append("Use ISM for complete technical analysis and trade decisions.")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines)


def format_radar_symbol_telegram(item: RadarItem) -> str:
    """Format a single symbol radar result for Telegram."""
    rsi_arrow = "\u2191" if item.rsi_change > 0 else "\u2193" if item.rsi_change < 0 else "\u2192"
    cat_emoji = _ACTIVITY_EMOJI.get(item.activity_category, "\u26aa")

    lines = [
        f"{cat_emoji} RADAR \u2014 {item.symbol}",
        f"{item.company_name}",
        "",
        f"Activity Score  : {item.activity_score}/100",
        f"Activity Level  : {item.activity_level}",
        f"Activity Type   : {item.activity_category}",
        f"Label           : {item.activity_label}",
        "",
        f"Price           : {item.price:.2f} ({item.price_change_percent:+.1f}%)",
        f"Volume          : {item.rvol_20:.1f}x average",
        f"Traded Value    : {item.traded_value:,.0f} EGP",
        "",
        f"RSI             : {item.rsi_14:.0f} {rsi_arrow} ({item.rsi_change:+.1f})",
        f"MACD Histogram  : {item.macd_histogram:.4f} ({item.macd_histogram_change:+.4f})",
        f"Close Location  : {item.close_location_value:.2f}",
    ]

    if item.adx_14 is not None:
        lines.append(f"ADX             : {item.adx_14:.1f}")

    if item.price_return_5d is not None:
        lines.append(f"5-Day Return    : {item.price_return_5d:+.1f}%")
    if item.price_return_20d is not None:
        lines.append(f"20-Day Return   : {item.price_return_20d:+.1f}%")

    lines.append("")
    lines.append("Reasons:")
    for r in item.reasons:
        lines.append(f"\u2022 {r}")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("Lite detects activity only.")
    lines.append("Use ISM for complete analysis.")

    return "\n".join(lines)
