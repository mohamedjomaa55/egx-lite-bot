"""
EGX Swing Scout v1.0 — Telegram Bot
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
from scanner import config
from scanner.decisions import DECISION_EMOJI

load_dotenv()

# ── Trading Hours Config ──────────────────────────────────────────────
CAIRO_TZ = pytz.timezone("Africa/Cairo")
TRADING_START = time(9, 35)
TRADING_END = time(14, 35)
TRADING_DAYS = [0, 1, 2, 3, 4]  # Sunday=0, Thursday=4

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
        [InlineKeyboardButton("🔍 مسح الأسهم (Scan)", callback_data="scan")],
        [InlineKeyboardButton("📊 آخر نتائج (Results)", callback_data="results")],
        [InlineKeyboardButton("📈 حالة السوق (Market)", callback_data="market")],
        [InlineKeyboardButton("📋 قائمة الأسهم (Stocks)", callback_data="stocks")],
        [InlineKeyboardButton("ℹ️ المساعدة (Help)", callback_data="help")],
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


# ── Start Command ─────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  EGX Swing Scout v1.0\n"
        "  أسهم الشريعة فقط\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "مرحبًا بك! اختر من القائمة:"
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
            "  القائمة الرئيسية\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "اختر من القائمة:"
        )
        await ctx.send(text, reply_markup=main_menu_keyboard())

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
        "  المساعدة\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔍 مسح الأسهم — تشغيل فحص كامل لأسهم الشريعة\n"
        "📊 آخر نتائج — عرض آخر مسح تم\n"
        "📈 حالة السوق — ملخص حالة السوق\n"
        "📋 قائمة الأسهم — جميع الأسهم المتاحة\n"
        "📋 [اسم السهم] — تفاصيل سهم معين\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🏆 نظام التقييم (0-100):\n"
        "  🟢 READY (≥90) — فرصة جاهزة للمراجعة\n"
        "  🟡 WATCH (≥80) — ينتظر اختراق\n"
        "  🟠 MONITOR (≥70) — على قائمة المتابعة\n"
        "  🔴 IGNORE (<70) — لا يصلح حالياً\n\n"
        "⚠️ البوت لا يصدر توصيات شراء أو بيع\n"
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
        BotCommand("start", "بدء البوت / القائمة الرئيسية"),
        BotCommand("scan", "مسح أسهم الشريعة"),
        BotCommand("results", "آخر نتائج المسح"),
        BotCommand("market", "حالة السوق"),
        BotCommand("stocks", "قائمة الأسهم"),
        BotCommand("help", "المساعدة"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Menu commands set successfully")


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "❌ BOT_TOKEN غير موجود.\n"
            "ضع قيمة في ملف .env:\n"
            "BOT_TOKEN=your_token_here"
        )

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("scan", scan_command))
    application.add_handler(CommandHandler("results", results_command))
    application.add_handler(CommandHandler("market", market_command))
    application.add_handler(CommandHandler("stocks", stocks_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("EGX Swing Scout Bot بدأ...")
    print("✅ EGX Swing Scout Bot بدأ بنجاح...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
