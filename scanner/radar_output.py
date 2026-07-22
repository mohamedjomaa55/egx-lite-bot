"""
Radar Output — Console and Telegram formatters for Market Radar.
"""

from __future__ import annotations

import math
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

TELEGRAM_MAX_LENGTH = 4096

_CAT_LABEL = {
    ActivityCategory.BUYING: "BUYING SIGNALS",
    ActivityCategory.SELLING: "SELLING SIGNALS",
    ActivityCategory.UNUSUAL: "WATCHLIST ACTIVITY",
}

_CAT_EMOJI = {
    ActivityCategory.BUYING: "\U0001f7e2",
    ActivityCategory.SELLING: "\U0001f534",
    ActivityCategory.UNUSUAL: "\U0001f7e1",
}

_LABEL_DISPLAY = {
    "Strong buying activity": "\U0001f3af STRONG BUYING",
    "Possible accumulation": "\U0001f3af POSSIBLE ACCUMULATION",
    "Moderate buying activity": "\U0001f3af MODERATE BUYING",
    "Strong selling pressure": "\u26a0\ufe0f STRONG SELLING",
    "Possible distribution": "\u26a0\ufe0f POSSIBLE DISTRIBUTION",
    "Moderate selling activity": "\u26a0\ufe0f MODERATE SELLING",
    "Unusual activity \u2014 direction unclear": "\U0001f440 DIRECTION UNCLEAR",
}

_STATUS_DISPLAY = {
    config.FRESHNESS_CURRENT: ("\u2705 CLOSED", False),
    config.FRESHNESS_PROVIDER_DELAYED: ("\u26a0\ufe0f DELAYED", True),
    config.FRESHNESS_MARKET_OPEN: ("\U0001f7e2 OPEN", False),
    config.FRESHNESS_NON_TRADING_DAY: ("\u26aa CLOSED", False),
    config.FRESHNESS_DATA_UNAVAILABLE: ("\U0001f534 DATA UNAVAILABLE", False),
}

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

_FRESHNESS_LABEL = {
    config.FRESHNESS_CURRENT: "CURRENT",
    config.FRESHNESS_PROVIDER_DELAYED: "PROVIDER_DELAYED",
    config.FRESHNESS_MARKET_OPEN: "MARKET_OPEN",
    config.FRESHNESS_NON_TRADING_DAY: "NON_TRADING_DAY",
    config.FRESHNESS_DATA_UNAVAILABLE: "DATA_UNAVAILABLE",
}

_FRESHNESS_STYLE = {
    config.FRESHNESS_CURRENT: "green",
    config.FRESHNESS_PROVIDER_DELAYED: "bold yellow",
    config.FRESHNESS_MARKET_OPEN: "bold cyan",
    config.FRESHNESS_NON_TRADING_DAY: "dim",
    config.FRESHNESS_DATA_UNAVAILABLE: "bold red",
}

_SEP = "\u2501" * 24


# ─── Card-Style Telegram Helpers ──────────────────────────────────────
def build_activity_bar(score: int, category: str) -> str:
    filled = round(score / 10)
    filled = max(0, min(10, filled))
    empty = 10 - filled

    if category == ActivityCategory.BUYING:
        bar = "\U0001f7e9" * filled + "\u2b1c" * empty
        label = f"{score}%"
        return f"{bar} {label}"
    elif category == ActivityCategory.SELLING:
        filled_full = score // 10
        remainder = score % 10
        has_partial = remainder >= 5
        full_blocks = filled_full if not has_partial else filled_full + 1
        full_blocks = max(0, min(10, full_blocks))
        empty = 10 - full_blocks
        bar = "\U0001f534" * full_blocks + "\U0001f7e7" * empty
        return f"{bar} {score}%"
    else:
        bar = "\U0001f7e8" * filled + "\u2b1c" * empty
        return f"{bar} {score}%"


def format_market_status(freshness_status: str) -> str:
    display = _STATUS_DISPLAY.get(freshness_status, ("\u26aa UNKNOWN", False))
    return display[0]


def _has_delay(freshness_status: str) -> bool:
    display = _STATUS_DISPLAY.get(freshness_status, ("", False))
    return display[1]


def _date_dd_mon_yyyy(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%d %b %Y")
    except (ValueError, TypeError):
        return date_str


def format_short_reason(reason: str, category: str) -> str:
    lower = reason.lower()
    if "rvol" in lower and "versus" in lower:
        return "High relative volume"
    if "volume" in lower and "average" in lower:
        return "High relative volume"
    if "traded value" in lower and "above" in lower:
        return "Strong traded value"
    if "traded value" in lower and "below" in lower:
        return "Weak traded value"
    if "rsi" in lower and "rose" in lower:
        return "RSI rising"
    if "rsi" in lower and "fell" in lower:
        return "RSI falling"
    if "close finished near the session high" in lower:
        return "Closed near session high"
    if "close finished near the session low" in lower:
        return "Closed near session low"
    if "macd histogram improving" in lower:
        return "MACD improving"
    if "macd histogram weakening" in lower:
        return "MACD weakening"
    if "high volume with limited price" in lower:
        return "Volume without movement"
    if "price rose" in lower:
        return f"Price up {reason.split('rose')[1].split('on')[0].strip()}"
    if "price fell" in lower:
        return f"Price down {reason.split('fell')[1].split('on')[0].strip()}"
    if "volume at" in lower and "percentile" in lower:
        return "Volume at high percentile"
    if "5-day return" in lower:
        return "5-day return"
    return reason


def format_stock_card(item: RadarItem, category: str, rank: int) -> str:
    cat_emoji = _CAT_EMOJI.get(category, "\u26aa")
    bar = build_activity_bar(item.activity_score, category)

    chg_pct = item.price_change_percent
    chg_icon = "\u2705" if chg_pct >= 0 else "\u274c"

    lines = [
        _SEP,
        f"{cat_emoji} {rank}. {item.symbol}",
        "",
        bar,
        "",
        f"\U0001f4b0 {item.latest_close:.2f}  ({chg_pct:+.1f}%)",
        f"\u21b3 Open: {item.session_open:.2f}",
        "",
    ]

    if item.rvol_20 > 0:
        if item.rvol_20 >= 2.0:
            vol_icon = "\U0001f4c8"
        elif item.rvol_20 >= 1.0:
            vol_icon = "\U0001f4c8"
        else:
            vol_icon = "\U0001f4c9"
        lines.append(f"{vol_icon} RVOL: {item.rvol_20:.1f}x")

    if item.rsi_14 and item.rsi_14 != 50.0:
        rsi_arrow = "\u2191" if item.rsi_change > 0 else "\u2193" if item.rsi_change < 0 else "\u2192"
        lines.append(f"\U0001f4ca RSI: {item.rsi_14:.0f} {rsi_arrow}")

    if item.macd_histogram_change != 0:
        hist_label = "Improving" if item.macd_histogram_change > 0 else "Weakening"
        lines.append(f"\u26a1 MACD: {hist_label}")

    lines.append("")

    label_display = _LABEL_DISPLAY.get(item.activity_label, item.activity_label.upper())
    lines.append(label_display)

    if item.reasons:
        for r in item.reasons[:3]:
            short = format_short_reason(r, category)
            marker = chg_icon if category == ActivityCategory.BUYING and short in ("RSI rising", "MACD improving") else "\u274c" if category == ActivityCategory.SELLING and short in ("RSI falling", "MACD weakening") else "\u26a1"
            lines.append(f"{marker} {short}")

    return "\n".join(lines)


def format_radar_header(result: MarketRadarResult, top_n: int) -> str:
    status_text = format_market_status(result.freshness_status)
    session_date = _date_dd_mon_yyyy(result.data_date)

    buying = sum(1 for i in result.all_items if i.activity_category == ActivityCategory.BUYING)
    selling = sum(1 for i in result.all_items if i.activity_category == ActivityCategory.SELLING)
    unusual = sum(1 for i in result.all_items if i.activity_category == ActivityCategory.UNUSUAL)
    total_signals = len(result.items)

    lines = [
        _SEP,
        "\U0001f4e1 EGX LITE MARKET RADAR",
        "",
        f"\U0001f4c5 Session: {session_date}",
        f"{status_text.split(' ')[0]} Market: {status_text.split(' ', 1)[1] if ' ' in status_text else status_text}",
        f"\U0001f4ca Scanned: {result.stats.symbols_scanned}",
        f"\U0001f3af Signals: {total_signals}",
        "",
        f"\U0001f7e2 BUY: {buying}   \U0001f534 SELL: {selling}   \U0001f7e1 WATCH: {unusual}",
        _SEP,
    ]

    if _has_delay(result.freshness_status):
        lines.insert(-1, f"\u26a0\ufe0f Provider delay: {result.freshness_note}")

    return "\n".join(lines)


def format_radar_footer() -> str:
    return (
        f"{_SEP}\n"
        "\u2139\ufe0f Lite detects market activity\n"
        "\U0001f4c8 Use ISM for Entry, Stop Loss and Targets\n"
        f"{_SEP}"
    )


def format_radar_category_section(category: str, items: list[RadarItem], start_rank: int = 1) -> str:
    cat_emoji = _CAT_EMOJI.get(category, "\u26aa")
    cat_label = _CAT_LABEL.get(category, "ACTIVITY")
    cards = [format_stock_card(item, category, start_rank + i) for i, item in enumerate(items)]
    header = f"{cat_emoji} {cat_label}"
    return header + "\n" + "\n".join(cards)


def split_radar_messages(parts: list[str], max_length: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    if not parts:
        return ["\u26a0\ufe0f No significant activity detected in this scan."]

    messages = []
    current = ""

    for part in parts:
        candidate = current + ("\n" if current else "") + part
        if len(candidate) <= max_length:
            current = candidate
        elif current:
            messages.append(current)
            current = part
        else:
            while len(part) > max_length:
                messages.append(part[:max_length])
                part = part[max_length:]
            current = part

    if current:
        messages.append(current)

    return messages if messages else ["\u26a0\ufe0f No significant activity detected in this scan."]


def format_radar_telegram_v2(result: MarketRadarResult, top_n: int | None = None) -> list[str]:
    if top_n is None:
        top_n = config.RADAR_TOP_N

    items = result.items[:top_n]

    parts = [format_radar_header(result, top_n)]

    categories = [
        (ActivityCategory.BUYING, "\U0001f7e2"),
        (ActivityCategory.SELLING, "\U0001f534"),
        (ActivityCategory.UNUSUAL, "\U0001f7e1"),
    ]

    for cat, emoji in categories:
        cat_items = [i for i in items if i.activity_category == cat]
        if cat_items:
            cat_header = f"{emoji} {_CAT_LABEL[cat]}"
            parts.append(cat_header)
            for i, item in enumerate(cat_items, 1):
                parts.append(format_stock_card(item, cat, i))

    if not items:
        parts.append("\u26a0\ufe0f No significant activity detected in this scan.")

    parts.append(format_radar_footer())

    return split_radar_messages(parts)


# ─── Console Output ──────────────────────────────────────────────────
def print_radar_header(result: MarketRadarResult):
    """Print the radar scan header."""
    stats = result.stats
    freshness_label = _FRESHNESS_LABEL.get(result.freshness_status, result.freshness_status)
    freshness_style = _FRESHNESS_STYLE.get(result.freshness_status, "")

    data_label = (
        "Data Mode (delayed)  :"
        if result.freshness_status == config.FRESHNESS_PROVIDER_DELAYED
        else "Data Mode           :"
    )

    lines = [
        "=" * 60,
        "",
        "    EGX LITE MARKET RADAR",
        "",
        f"  {data_label} {result.data_mode}",
        f"  Data Date           : {result.data_date}",
        f"  Expected Session    : {result.expected_latest_session}",
        f"  Freshness           : {freshness_label}",
        f"  Freshness Note      : {result.freshness_note}",
    ]

    if result.freshness_status == config.FRESHNESS_PROVIDER_DELAYED:
        lines.append(f"  Provider Delay Days : {result.freshness_delay_days}")

    lines.extend([
        "",
        f"  Scanned             : {stats.symbols_scanned}",
        f"  Activity Found      : {stats.activity_detected}",
        f"  Buying Activity     : {stats.buying_count}",
        f"  Selling Activity    : {stats.selling_count}",
        f"  Unusual Activity    : {stats.unusual_count}",
        f"  Failed              : {stats.failed_count}",
        f"  Skipped (illiq)     : {stats.skipped_illiquid}",
        f"  Duration            : {stats.scan_duration}s",
        "",
        "=" * 60,
    ])
    console.print("\n".join(lines))

    if result.freshness_status == config.FRESHNESS_PROVIDER_DELAYED:
        console.print(
            f"\n  ⚠ Provider delay detected — data is {result.freshness_note}",
            style="bold yellow",
        )
        console.print("  ⚠ Activity signals may be based on stale data", style="bold yellow")
        console.print("")


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
        table.add_column("Close", justify="right", width=8)
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
                f"{item.latest_close:.2f}",
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
        console.print(f"  Close: {item.latest_close:.2f} ({item.price_change_percent:+.1f}%)  Open: {item.session_open:.2f}  Volume: {item.rvol_20:.1f}x  RSI: {item.rsi_14:.0f}")
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

    freshness_label = _FRESHNESS_LABEL.get(result.freshness_status, result.freshness_status)

    data_label = (
        "Data: Latest available completed session"
        if result.freshness_status == config.FRESHNESS_PROVIDER_DELAYED
        else "Data: Latest completed session"
    )

    lines = [
        "\U0001f4e1 EGX LITE MARKET RADAR v2.0-session-freshness-fix",
        data_label,
        f"Date: {result.data_date}",
        f"Expected: {result.expected_latest_session}",
        f"Freshness: {freshness_label}",
    ]

    if result.freshness_status == config.FRESHNESS_PROVIDER_DELAYED:
        lines.append(f"Provider Delay Days: {result.freshness_delay_days}")

    lines.extend([
        f"Scanned: {stats.symbols_scanned}",
        f"Activity detected: {stats.activity_detected}",
        "",
    ])

    if result.freshness_status == config.FRESHNESS_PROVIDER_DELAYED:
        lines.append(f"⚠ Provider delay: {result.freshness_note}")
        lines.append("⚠ Activity signals may be based on stale data")
        lines.append("")

    # ── Buying Activity ───────────────────────────────────────────────
    buying = [i for i in items if i.activity_category == ActivityCategory.BUYING]
    if buying:
        lines.append("\U0001f7e2 BUYING ACTIVITY")
        lines.append("")
        for i, item in enumerate(buying, 1):
            rsi_arrow = "\u2191" if item.rsi_change > 0 else "\u2193" if item.rsi_change < 0 else "\u2192"
            lines.append(f"{i}. {item.symbol} \u2014 Activity {item.activity_score}/100")
            lines.append(f"Last Completed Close: {item.latest_close:.2f} ({item.price_change_percent:+.1f}%)")
            lines.append(f"Session Open: {item.session_open:.2f}")
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
            lines.append(f"Last Completed Close: {item.latest_close:.2f} ({item.price_change_percent:+.1f}%)")
            lines.append(f"Session Open: {item.session_open:.2f}")
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
            lines.append(f"Last Completed Close: {item.latest_close:.2f} ({item.price_change_percent:+.1f}%)")
            lines.append(f"Session Open: {item.session_open:.2f}")
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
    freshness_label = _FRESHNESS_LABEL.get(item.freshness_status, item.freshness_status)

    lines = [
        f"{cat_emoji} RADAR \u2014 {item.symbol}",
        f"{item.company_name}",
        "",
        f"Activity Score  : {item.activity_score}/100",
        f"Activity Level  : {item.activity_level}",
        f"Activity Type   : {item.activity_category}",
        f"Label           : {item.activity_label}",
        "",
        f"Last Completed Close : {item.latest_close:.2f} ({item.price_change_percent:+.1f}%)",
        f"Session Open         : {item.session_open:.2f}",
        f"Session High         : {item.session_high:.2f}",
        f"Session Low          : {item.session_low:.2f}",
        f"Previous Close       : {item.previous_close:.2f}",
        f"Session Date         : {item.price_date}",
        f"Data Mode            : {item.data_mode}",
        "",
        f"Provider Latest      : {item.provider_latest_date}",
        f"Expected Session     : {item.expected_latest_session}",
        f"Freshness            : {freshness_label}",
        f"Freshness Note       : {item.freshness_note}",
        "",
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
