"""
EGX Lite Market Radar v2.0 — Telegram Bot
Shariah-compliant stocks only.
Trading hours: Sunday-Thursday, 9:35 AM - 2:35 PM (Cairo time, UTC+2)
"""

import os
import sys
import asyncio
import logging
import pytz
from datetime import datetime, time
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from scanner.scanner import scan as run_scan
from scanner.market_radar import run_market_radar, ActivityCategory, ActivityLevel
from scanner.ism_handoff import create_handoff
from scanner import config
from scanner.config import DATA_PROVIDER
from scanner.decisions import DECISION_EMOJI
from scanner.radar_output import format_radar_telegram, format_radar_symbol_telegram, _FRESHNESS_LABEL
from providers.egxapi_provider import get_provider as get_egxapi, QuoteState

load_dotenv()

# ── Version ──────────────────────────────────────────────────────────
RADAR_VERSION = "2.0-session-freshness-fix"

# ── Trading Hours Config ──────────────────────────────────────────────
CAIRO_TZ = pytz.timezone("Africa/Cairo")
TRADING_START = time(9, 35)
TRADING_END = time(14, 35)
TRADING_DAYS = [0, 1, 2, 3, 6]  # Sunday=6, Monday=0, Tuesday=1, Wednesday=2, Thursday=3

TOKEN = os.getenv("BOT_TOKEN", "")
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ── Context Wrapper ───────────────────────────────────────────────────
class MsgContext:
    """Unified wrapper for message and callback query."""
    def __init__(self, update: Update):
        self.update = update
        if update.callback_query:
            self.query = update.callback_query
            self.is_callback = True
        else:
            self.query = None
            self.is_callback = False

    async def answer(self):
        if self.is_callback:
            await self.query.answer()

    async def send(self, text: str, reply_markup=None):
        if self.is_callback:
            try:
                await self.query.edit_message_text(text, reply_markup=reply_markup)
            except Exception:
                await self.query.message.reply_text(text, reply_markup=reply_markup)
        else:
            await self.update.message.reply_text(text, reply_markup=reply_markup)


# ── Trading Hours Check ───────────────────────────────────────────────
def is_trading_hours() -> bool:
    now = datetime.now(CAIRO_TZ)
    if now.weekday() not in TRADING_DAYS:
        return False
    return TRADING_START <= now.time() <= TRADING_END


def get_next_trading_session() -> str:
    now = datetime.now(CAIRO_TZ)
    current_time = now.time()
    current_day = now.weekday()

    if current_day in TRADING_DAYS and current_time < TRADING_START:
        return f"اليوم {now.strftime('%A')} الساعة 9:35 ص"

    days_until_next = 0
    next_day = current_day
    while True:
        next_day = (next_day + 1) % 7
        days_until_next += 1
        if next_day in TRADING_DAYS:
            break
        if days_until_next > 6:
            break

    if days_until_next == 1:
        return "بكره الساعة 9:35 ص"
    elif days_until_next == 2:
        return "بعد بكره الساعة 9:35 ص"
    else:
        return f"بعد {days_until_next} يوم الساعة 9:35 ص"


# ── Menu Keyboard ─────────────────────────────────────────────────────
def main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("\U0001f4e1 Market Radar", callback_data="radar")],
        [InlineKeyboardButton("\U0001f7e2 Buying Activity", callback_data="radar_buying")],
        [InlineKeyboardButton("\U0001f534 Selling Activity", callback_data="radar_selling")],
        [InlineKeyboardButton("\U0001f7e1 Unusual Activity", callback_data="radar_unusual")],
        [InlineKeyboardButton("\U0001f4ca Legacy Scan", callback_data="scan")],
        [InlineKeyboardButton("\U0001f4c8 Market", callback_data="market")],
        [InlineKeyboardButton("\U0001f4cb Stocks", callback_data="stocks")],
        [InlineKeyboardButton("\u2139\ufe0f Help", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)


def back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="menu")]]
    )


# ── Scan Cache ────────────────────────────────────────────────────────
_last_scan: dict = {
    "results": None,
    "market": None,
    "stats": None,
    "elapsed": None,
    "timestamp": None,
}

# ── Radar Cache ───────────────────────────────────────────────────────
_last_radar: dict = {
    "result": None,
    "timestamp": None,
}


# ── Radar Commands ────────────────────────────────────────────────────
async def radar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /radar [N] — run market radar."""
    ctx = MsgContext(update)
    top_n = 20
    if context.args:
        try:
            top_n = int(context.args[0])
            top_n = max(1, min(top_n, 50))
        except ValueError:
            pass
    await handle_radar(ctx, top_n=top_n)


async def radar_buying_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /radar_buying — show BUYING_ACTIVITY stocks."""
    ctx = MsgContext(update)
    await handle_radar_category(ctx, ActivityCategory.BUYING)


async def radar_selling_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /radar_selling — show SELLING_ACTIVITY stocks."""
    ctx = MsgContext(update)
    await handle_radar_category(ctx, ActivityCategory.SELLING)


async def radar_unusual_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /radar_unusual — show UNUSUAL_ACTIVITY stocks."""
    ctx = MsgContext(update)
    await handle_radar_category(ctx, ActivityCategory.UNUSUAL)


async def radar_symbol_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /radar_symbol <SYMBOL> — single symbol radar analysis."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /radar_symbol ARCC\n"
            "Returns the Lite radar analysis for one symbol."
        )
        return

    symbol = args[0].strip().upper()
    await update.message.reply_text(f"\U0001f50d Running radar analysis for {symbol}...")

    try:
        result = await asyncio.to_thread(
            run_market_radar, symbols=[symbol], top_n=1,
        )
        if not result.items:
            await update.message.reply_text(
                f"\u26a0\ufe0f {symbol} — no activity detected or insufficient data."
            )
            return

        item = result.items[0]
        text = format_radar_symbol_telegram(item)
        await update.message.reply_text(text)

    except Exception as e:
        logger.error("Radar symbol failed: %s", e)
        await update.message.reply_text(f"\u274c Error: {e}")


async def send_to_ism_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /send_to_ism <SYMBOL> — prepare ISM handoff."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /send_to_ism ARCC\n"
            "Prepares the selected symbol for ISM deep analysis."
        )
        return

    symbol = args[0].strip().upper()

    # Check if we have radar data for this symbol
    radar_item = None
    if _last_radar["result"]:
        for item in _last_radar["result"].all_items:
            if item.symbol == symbol:
                radar_item = item
                break

    if radar_item is None:
        # Run a quick radar scan for this symbol
        try:
            result = await asyncio.to_thread(
                run_market_radar, symbols=[symbol], top_n=1,
            )
            if result.items:
                radar_item = result.items[0]
        except Exception as e:
            logger.error("ISM handoff radar failed: %s", e)

    if radar_item is None:
        await update.message.reply_text(
            f"\u26a0\ufe0f {symbol} — no radar data available. Run /radar first."
        )
        return

    handoff = create_handoff(radar_item)
    text = handoff.to_command_text()

    await update.message.reply_text(text)


# ── Radar Handlers ────────────────────────────────────────────────────
async def handle_radar(ctx: MsgContext, top_n: int = 20) -> None:
    """Run market radar and send results."""
    await ctx.send(
        "\U0001f4e1 Running Market Radar...\n\u23f3 Scanning EGX symbols..."
    )

    try:
        result = await asyncio.to_thread(run_market_radar, top_n=top_n)

        _last_radar["result"] = result
        _last_radar["timestamp"] = datetime.now()

        text = format_radar_telegram(result, top_n=top_n)
        await ctx.send(text)

    except Exception as e:
        logger.error("Radar scan failed: %s", e)
        await ctx.send(f"\u274c Radar scan failed: {e}", reply_markup=back_button())


async def handle_radar_category(ctx: MsgContext, category: str) -> None:
    """Show stocks from a specific activity category."""
    # Use cached radar if available, otherwise run fresh
    if _last_radar["result"] is not None:
        result = _last_radar["result"]
    else:
        await ctx.send("\U0001f4e1 Running Market Radar...")
        try:
            result = await asyncio.to_thread(run_market_radar, top_n=50)
            _last_radar["result"] = result
            _last_radar["timestamp"] = datetime.now()
        except Exception as e:
            logger.error("Radar scan failed: %s", e)
            await ctx.send(f"\u274c Radar scan failed: {e}", reply_markup=back_button())
            return

    category_items = [
        i for i in result.all_items if i.activity_category == category
    ]

    cat_emoji = {
        ActivityCategory.BUYING: "\U0001f7e2",
        ActivityCategory.SELLING: "\U0001f534",
        ActivityCategory.UNUSUAL: "\U0001f7e1",
    }.get(category, "\u26aa")

    cat_name = {
        ActivityCategory.BUYING: "BUYING ACTIVITY",
        ActivityCategory.SELLING: "SELLING ACTIVITY",
        ActivityCategory.UNUSUAL: "UNUSUAL ACTIVITY",
    }.get(category, "ACTIVITY")

    freshness_label = _FRESHNESS_LABEL.get(result.freshness_status, result.freshness_status)

    lines = [
        f"{cat_emoji} {cat_name}",
        f"Date: {result.data_date}",
        f"Expected: {result.expected_latest_session}",
        f"Freshness: {freshness_label}",
    ]

    if result.freshness_status == config.FRESHNESS_PROVIDER_DELAYED:
        lines.append(f"Provider Delay Days: {result.freshness_delay_days}")

    lines.extend([
        f"Found: {len(category_items)}",
        "",
    ])

    for i, item in enumerate(category_items[:15], 1):
        rsi_arrow = "\u2191" if item.rsi_change > 0 else "\u2193" if item.rsi_change < 0 else "\u2192"
        lines.append(f"{i}. {item.symbol} \u2014 Activity {item.activity_score}/100")
        lines.append(f"Last Completed Close: {item.latest_close:.2f} ({item.price_change_percent:+.1f}%)")
        lines.append(f"Volume: {item.rvol_20:.1f}x | RSI: {item.rsi_14:.0f} {rsi_arrow}")
        lines.append(f"Label: {item.activity_label}")
        if item.reasons:
            for r in item.reasons[:2]:
                lines.append(f"\u2022 {r}")
        lines.append("")

    if not category_items:
        lines.append("No stocks in this category.")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("Lite detects activity only.")
    lines.append("Use ISM for complete analysis.")

    await ctx.send("\n".join(lines), reply_markup=back_button())


# ── Start Command ─────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  EGX Lite Market Radar {RADAR_VERSION}\n"
        "  Shariah-compliant stocks\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Market activity scanner.\n"
        "Detects unusual volume, buying/selling pressure.\n\n"
        "Choose from the menu:"
    )
    await update.message.reply_text(text, reply_markup=main_menu_keyboard())


# ── Callback Handler ──────────────────────────────────────────────────
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = MsgContext(update)
    await ctx.answer()

    data = update.callback_query.data

    if data == "menu":
        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "  EGX Lite Market Radar\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Choose from the menu:"
        )
        await ctx.send(text, reply_markup=main_menu_keyboard())

    elif data == "radar":
        await handle_radar(ctx)

    elif data == "radar_buying":
        await handle_radar_category(ctx, ActivityCategory.BUYING)

    elif data == "radar_selling":
        await handle_radar_category(ctx, ActivityCategory.SELLING)

    elif data == "radar_unusual":
        await handle_radar_category(ctx, ActivityCategory.UNUSUAL)

    elif data == "scan":
        await handle_scan(ctx)

    elif data == "results":
        await handle_results(ctx)

    elif data == "market":
        await handle_market(ctx)

    elif data == "stocks":
        await handle_stocks(ctx)

    elif data == "help":
        await handle_help(ctx)

    elif data.startswith("detail_"):
        ticker = data.replace("detail_", "")
        await handle_detail(ctx, ticker)


# ── Command Handlers ──────────────────────────────────────────────────
async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = MsgContext(update)
    await handle_scan(ctx)


async def results_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = MsgContext(update)
    await handle_results(ctx)


async def market_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = MsgContext(update)
    await handle_market(ctx)


async def stocks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = MsgContext(update)
    await handle_stocks(ctx)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = MsgContext(update)
    await handle_help(ctx)


# ── EGXAPI Status Command ────────────────────────────────────────────
async def egxapi_status_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /egxapi_status <SYMBOL>
    Compare EGXAPI price vs Yahoo Finance fallback.
    """
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /egxapi_status ARCC\n"
            "Compares EGXAPI price vs current fallback."
        )
        return

    symbol = args[0].strip().upper()
    await update.message.reply_text(f"🔄 Checking EGXAPI for {symbol}...")

    try:
        provider = get_egxapi()
        comp = await asyncio.to_thread(provider.compare_quote, symbol)

        # ── State emoji ───────────────────────────────────────────────
        state_emoji = {
            QuoteState.LIVE_VERIFIED.value: "🟢",
            QuoteState.DELAYED.value: "🟡",
            QuoteState.PRICE_MISMATCH.value: "🔴",
            QuoteState.INVALID_SYMBOL.value: "⚫",
            QuoteState.DATA_UNAVAILABLE.value: "⚫",
        }.get(comp.egxapi_state, "⚪")

        # ── Format diff ───────────────────────────────────────────────
        if comp.price_difference is not None:
            diff_str = (
                f"{comp.price_difference:+.2f} "
                f"({comp.price_difference_percent:+.2f}%)"
            )
        else:
            diff_str = "N/A"

        # ── Format timestamp age ──────────────────────────────────────
        if comp.timestamp_age_seconds is not None:
            age_s = comp.timestamp_age_seconds
            if age_s < 60:
                age_str = f"{age_s:.0f}s ago"
            elif age_s < 3600:
                age_str = f"{age_s / 60:.1f}m ago"
            else:
                age_str = f"{age_s / 3600:.1f}h ago"
        else:
            age_str = "N/A"

        # ── Build message ─────────────────────────────────────────────
        name = config.STOCK_NAMES.get(symbol, symbol)
        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  EGXAPI Status — {symbol} ({name})\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"  State: {state_emoji} {comp.egxapi_state}\n\n"
            f"  EGXAPI Price  : {comp.egxapi_price or 'N/A'}\n"
            f"  Fallback Price: {comp.fallback_price or 'N/A'}\n"
            f"  Source        : {comp.fallback_source}\n\n"
            f"  Difference    : {diff_str}\n"
            f"  Timestamp Age : {age_str}\n"
            f"  Bid / Ask     : {comp.egxapi_bid or 'N/A'} / {comp.egxapi_ask or 'N/A'}\n"
            f"  Volume        : {comp.egxapi_volume or 'N/A'}\n"
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ Shadow mode — EGXAPI not used for decisions\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        await update.message.reply_text(text)

    except Exception as e:
        logger.error("EGXAPI status failed: %s", e)
        await update.message.reply_text(f"❌ Error: {e}")


# ── Scan ──────────────────────────────────────────────────────────────
async def handle_scan(ctx: MsgContext) -> None:
    if not is_trading_hours():
        next_session = get_next_trading_session()
        text = (
            "⏰ خارج ساعات التداول\n\n"
            "📊 ساعات العمل: الأحد - الخميس\n"
            "⏰ 9:35 ص - 2:35 ظ\n\n"
            f"📅 الجلسة القادمة: {next_session}\n\n"
            "💡 يمكنك عرض آخر نتائج مسح من زرار '📊 آخر نتائج'"
        )
        await ctx.send(text, reply_markup=back_button())
        return

    await ctx.send("🔄 جاري مسح أسهم الشريعة...\n⏳ قد يستغرق دقيقة واحدة")

    try:
        results, mkt, stats, elapsed = await asyncio.to_thread(run_scan)

        _last_scan["results"] = results
        _last_scan["market"] = mkt
        _last_scan["stats"] = stats
        _last_scan["elapsed"] = elapsed
        _last_scan["timestamp"] = datetime.now()

        # ── Shadow provider comparison (non-blocking) ─────────────────
        if DATA_PROVIDER == "shadow":
            try:
                from providers.shadow import run_shadow_comparison
                shadow_tickers = [r["ticker"] for r in results]
                if shadow_tickers:
                    shadow_summary = run_shadow_comparison(
                        tickers=shadow_tickers,
                        scan_results=results,
                    )
                    logger.info(
                        "Shadow comparison: %s",
                        shadow_summary.to_text(),
                    )
            except Exception as exc:
                logger.warning("Shadow comparison failed (non-blocking): %s", exc)

        ready = sum(1 for r in results if r["decision"] == "READY")
        watch = sum(1 for r in results if r["decision"] == "WATCH")
        monitor = sum(1 for r in results if r["decision"] == "MONITOR")
        ignore = sum(1 for r in results if r["decision"] == "IGNORE")

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "  نتائج المسح\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"📈 السوق: {_market_emoji(mkt['status'])} {mkt['status']}\n"
            f"⏱️ الوقت: {elapsed:.1f} ثانية\n\n"
            f"📊 الإحصائيات:\n"
            f"  • طُلب: {stats.stocks_requested}\n"
            f"  • تم التحليل: {stats.stocks_analyzed}\n"
            f"  • فشل البيانات: {stats.data_failures}\n"
            f"  • مر بالفلاتر: {stats.passed_filters}\n\n"
            f"🟢 READY: {ready}\n"
            f"🟡 WATCH: {watch}\n"
            f"🟠 MONITOR: {monitor}\n"
            f"🔴 IGNORE: {ignore}\n"
        )

        if mkt.get("warning"):
            text += f"\n⚠️ {mkt['warning']}\n"

        text += "\n━━ أفضل 10 ━━\n"

        top = results[:10]
        if not top:
            text += "\n⚠️ لا توجد نتائج مناسبة"
        else:
            for i, r in enumerate(top, 1):
                emoji = DECISION_EMOJI.get(r["decision"], "")
                name = config.STOCK_NAMES.get(r["ticker"], r["ticker"])
                text += (
                    f"\n{i}. {r['ticker']} — {name}\n"
                    f"   💰 {r['close']:.2f} | 🏆 {r['score']}/100 {emoji} {r['decision']}\n"
                    f"   📉 RSI: {r['rsi']:.1f} | 📊 RVOL: {r['rvol']:.2f}\n"
                )

        text += (
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ هذه معلومات فقط، ليست توصية بشراء أو بيع\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        keyboard = []
        for r in top[:10]:
            keyboard.append(
                [InlineKeyboardButton(
                    f"📋 {r['ticker']} — {config.STOCK_NAMES.get(r['ticker'], '')}",
                    callback_data=f"detail_{r['ticker']}"
                )]
            )
        keyboard.append([InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="menu")])

        await ctx.send(text, reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Scan failed: {e}")
        await ctx.send(f"❌ فشل المسح: {e}", reply_markup=back_button())


# ── Results ───────────────────────────────────────────────────────────
async def handle_results(ctx: MsgContext) -> None:
    if _last_scan["results"] is None:
        await ctx.send(
            "⚠️ لا توجد نتائج بعد.\nاستخدم مسح الأسهم أولاً.",
            reply_markup=back_button(),
        )
        return

    results = _last_scan["results"]
    ts = _last_scan["timestamp"].strftime("%Y-%m-%d %H:%M")

    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  آخر نتائج ({ts})\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )

    top = results[:10]
    if not top:
        text += "\n⚠️ لا توجد نتائج"
    else:
        for i, r in enumerate(top, 1):
            emoji = DECISION_EMOJI.get(r["decision"], "")
            name = config.STOCK_NAMES.get(r["ticker"], r["ticker"])
            text += (
                f"\n{i}. {r['ticker']} — {name}\n"
                f"   💰 {r['close']:.2f} | 🏆 {r['score']}/100 {emoji} {r['decision']}\n"
                f"   📉 RSI: {r['rsi']:.1f} | 📊 RVOL: {r['rvol']:.2f}\n"
            )

    text += (
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ معلومات فقط، ليست توصية\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    keyboard = []
    for r in top[:10]:
        keyboard.append(
            [InlineKeyboardButton(
                f"📋 {r['ticker']}",
                callback_data=f"detail_{r['ticker']}"
            )]
        )
    keyboard.append([InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="menu")])

    await ctx.send(text, reply_markup=InlineKeyboardMarkup(keyboard))


# ── Market ────────────────────────────────────────────────────────────
async def handle_market(ctx: MsgContext) -> None:
    if _last_scan["market"] is None:
        await ctx.send(
            "⚠️ لا توجد بيانات سوق بعد.\nاستخدم مسح الأسهم أولاً.",
            reply_markup=back_button(),
        )
        return

    mkt = _last_scan["market"]

    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  حالة السوق\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📈 الحالة: {_market_emoji(mkt['status'])} {mkt['status']}\n"
        f"📊 السعر: {mkt.get('index_close', 'N/A')}\n"
        f"📈 EMA50: {mkt.get('ema50', 'N/A')}\n"
        f"📉 EMA200: {mkt.get('ema200', 'N/A')}\n"
        f"🔄 EMA200 Slope: {mkt.get('ema200_slope', 'N/A')}\n"
        f"📋 المستخدمين: {mkt.get('tickers_used', 'N/A')}\n"
    )

    if mkt.get("warning"):
        text += f"\n⚠️ {mkt['warning']}\n"

    text += (
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ معلومات فقط، ليست توصية\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    await ctx.send(text, reply_markup=back_button())


# ── Stocks List ───────────────────────────────────────────────────────
async def handle_stocks(ctx: MsgContext) -> None:
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  قائمة الأسهم ({len(config.EGX_SYMBOL_MAP)} سهم)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )

    for ticker, name in config.STOCK_NAMES.items():
        text += f"• {ticker} — {name}\n"

    text += (
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "جميع الأسهم متوافقة مع الشريعة الإسلامية\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    await ctx.send(text, reply_markup=back_button())


# ── Help ──────────────────────────────────────────────────────────────
async def handle_help(ctx: MsgContext) -> None:
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  Help\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Commands:\n"
        "/radar — Run market radar\n"
        "/radar 10 — Radar, top 10\n"
        "/radar_buying — Buying activity\n"
        "/radar_selling — Selling activity\n"
        "/radar_unusual — Unusual activity\n"
        "/radar_symbol ARCC — Single symbol\n"
        "/send_to_ism ARCC — ISM handoff\n\n"
        "Legacy:\n"
        "/scan — Legacy scan\n"
        "/results — Last results\n"
        "/market — Market status\n"
        "/stocks — Stock list\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Activity Levels:\n"
        "  EXTREME — RVOL >= 3.0\n"
        "  HIGH — RVOL >= 2.0\n"
        "  ELEVATED — RVOL >= 1.35\n"
        "  NORMAL — Below thresholds\n\n"
        "Categories:\n"
        "  BUYING — Positive signals\n"
        "  SELLING — Negative signals\n"
        "  UNUSUAL — Mixed signals\n\n"
        "Lite detects activity only.\n"
        "Use ISM for trade decisions.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    await ctx.send(text, reply_markup=back_button())


# ── Stock Detail ──────────────────────────────────────────────────────
async def handle_detail(ctx: MsgContext, ticker: str) -> None:
    if _last_scan["results"] is None:
        await ctx.send(
            "⚠️ لا توجد نتائج بعد.\nاستخدم مسح الأسهم أولاً.",
            reply_markup=back_button(),
        )
        return

    result = next((r for r in _last_scan["results"] if r["ticker"] == ticker), None)
    if result is None:
        await ctx.send(
            f"⚠️ لم يتم العثور على {ticker} في آخر مسح.",
            reply_markup=back_button(),
        )
        return

    emoji = DECISION_EMOJI.get(result["decision"], "")
    name = config.STOCK_NAMES.get(ticker, ticker)

    trend_str = "صاعد ✅" if result["trend"]["above_ema200"] and result["trend"]["ema_aligned"] else \
                "هابط ❌" if not result["trend"]["above_ema200"] else "مختلط ⚠️"
    macd_str = "صاعد ✅" if result["macd_bullish"] else "هابط ❌"

    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {ticker} — {name}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 السعر: {result['close']:.2f} EGP\n"
        f"🏆 التقييم: {result['score']}/100 {emoji} {result['decision']}\n\n"
        f"━━ المؤشرات ━━\n"
        f"📉 RSI: {result['rsi']:.1f}\n"
        f"📊 RVOL: {result['rvol']:.2f}\n"
        f"📈 EMA50: {result['ema50']:.2f}\n"
        f"📉 EMA200: {result['ema200']:.2f}\n"
        f"🔄 MACD: {macd_str}\n"
        f"📊 الاتجاه: {trend_str}\n\n"
        f"━━ المقاومة ━━\n"
        f"🔴 المقاومة: {result.get('resistance', 'N/A')}\n"
        f"📏 المسافة: {result.get('resistance_dist_pct', 0):.2f}%\n\n"
        f"━━ VPVR ━━\n"
        f"📍 POC: {result['vp'].get('poc', 'N/A')}\n"
        f"📏 المسافة من POC: {result['vp'].get('distance_from_poc_pct', 0):.2f}%\n\n"
        f"━━ المخاطر ━━\n"
        f"🛑 وقف الخسارة: {result.get('suggested_stop', 0):.2f}\n"
        f"⚠️ نسبة المخاطر: {result.get('risk_pct', 0):.2f}%\n\n"
        f"━━ الأسباب ━━\n"
    )

    for reason in result.get("reasons", []):
        text += f"• {reason}\n"

    text += (
        f"\n💡 الإجراء المقترح: {result.get('next_action', 'N/A')}\n"
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ معلومات فقط، ليست توصية\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    await ctx.send(text, reply_markup=back_button())


# ── Helpers ───────────────────────────────────────────────────────────
def _market_emoji(status: str) -> str:
    return {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(status, "⚪")


# ── Main ──────────────────────────────────────────────────────────────
async def post_init(application: Application) -> None:
    """Set up menu commands after bot initialization."""
    commands = [
        BotCommand("start", "Start / Menu"),
        BotCommand("radar", "Run Market Radar"),
        BotCommand("radar_buying", "Buying activity"),
        BotCommand("radar_selling", "Selling activity"),
        BotCommand("radar_unusual", "Unusual activity"),
        BotCommand("radar_symbol", "Single symbol radar"),
        BotCommand("send_to_ism", "Prepare ISM handoff"),
        BotCommand("scan", "Legacy scan (deprecated)"),
        BotCommand("results", "Last scan results"),
        BotCommand("market", "Market status"),
        BotCommand("stocks", "Stock list"),
        BotCommand("help", "Help"),
        BotCommand("egxapi_status", "EGXAPI status"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Menu commands set successfully")


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "BOT_TOKEN not found.\n"
            "Set in .env:\n"
            "BOT_TOKEN=your_token_here"
        )

    # ── Startup Diagnostics ──────────────────────────────────────────
    import subprocess
    project_path = os.path.dirname(os.path.abspath(__file__))
    radar_output_path = os.path.join(project_path, "scanner", "radar_output.py")
    print("=" * 60)
    print(f"  EGX LITE MARKET RADAR {RADAR_VERSION}")
    print("=" * 60)
    print(f"  Project Path      : {project_path}")
    print(f"  radar_output.py   : {radar_output_path}")
    print(f"  radar_output.py exists: {os.path.exists(radar_output_path)}")
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=project_path, stderr=subprocess.DEVNULL
        ).decode().strip()
        commit = subprocess.check_output(
            ["git", "log", "--oneline", "-1"],
            cwd=project_path, stderr=subprocess.DEVNULL
        ).decode().strip()
        print(f"  Git Branch        : {branch}")
        print(f"  Git Commit        : {commit}")
    except Exception as e:
        print(f"  Git info unavailable: {e}")
    print(f"  Python            : {sys.version}")
    print(f"  Trading Days      : {TRADING_DAYS} (Sun-Thu)")
    print(f"  Version           : {RADAR_VERSION}")
    print("=" * 60)
    print()

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("radar", radar_command))
    application.add_handler(CommandHandler("radar_buying", radar_buying_command))
    application.add_handler(CommandHandler("radar_selling", radar_selling_command))
    application.add_handler(CommandHandler("radar_unusual", radar_unusual_command))
    application.add_handler(CommandHandler("radar_symbol", radar_symbol_command))
    application.add_handler(CommandHandler("send_to_ism", send_to_ism_command))
    application.add_handler(CommandHandler("scan", scan_command))
    application.add_handler(CommandHandler("results", results_command))
    application.add_handler(CommandHandler("market", market_command))
    application.add_handler(CommandHandler("stocks", stocks_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("egxapi_status", egxapi_status_command))
    application.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("EGX Lite Market Radar Bot started...")
    print("EGX Lite Market Radar Bot started...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
