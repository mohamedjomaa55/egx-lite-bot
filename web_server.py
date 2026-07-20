"""
EGX Swing Scout — Render Web Server
Keeps the service alive and runs the Telegram bot.
"""

import os
import sys
import logging
import threading
from flask import Flask

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def home():
    return "EGX Swing Scout Bot is running!"


@app.route("/health")
def health():
    return {"status": "ok", "service": "egx-swing-scout"}


def run_bot():
    """Run the Telegram bot in a background thread."""
    try:
        from bot import main as bot_main
        bot_main()
    except Exception as e:
        logger.error(f"Bot crashed: {e}")


if __name__ == "__main__":
    # Start bot in background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    logger.info("Bot started in background thread")

    # Run Flask server (required by Render to keep service alive)
    port = int(os.getenv("PORT", 5000))
    logger.info(f"Starting web server on port {port}")
    app.run(host="0.0.0.0", port=port)
