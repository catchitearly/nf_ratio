"""
main.py
Entry point – called by GitHub Actions cron every 5 minutes.
Sets up logging, runs strategy, ensures clean exit.
"""

import logging
import os
import sys
import traceback
from datetime import datetime

# ── Logging setup ──────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
log_file = f"logs/{datetime.now().strftime('%Y-%m-%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("main")


def main():
    try:
        log.info("▶ main.py started")
        from strategy import run
        run()
        log.info("✓ main.py finished")
        sys.exit(0)
    except Exception as e:
        log.critical(f"FATAL: {e}\n{traceback.format_exc()}")
        try:
            from telegram_bot import send_error_alert
            send_error_alert(f"FATAL in main.py: {str(e)[:300]}")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
