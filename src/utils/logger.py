import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from src.utils.telegram_notifier import TelegramNotifier


class TelegramLoggingHandler(logging.Handler):
    def __init__(self, notifier: TelegramNotifier):
        super().__init__()
        self.notifier = notifier

    def emit(self, record):
        try:
            msg = self.format(record)
            # Only send if error or critical
            if record.levelno >= logging.ERROR:
                alert_msg = f"🚨 *System Error*\n```\n{msg}\n```"
                self.notifier.send_message(alert_msg)
            elif record.levelno == logging.WARNING:
                # alert_msg = f"⚠️ *System Warning*\n{msg}"
                self.notifier.send_message(msg)
        except Exception:
            self.handleError(record)


def setup_logger(name: str = "InvestmentFirmAlpha") -> logging.Logger:
    logger = logging.getLogger(name)

    # Avoid adding multiple handlers if logger is already configured
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 2. File Handler (Rotating)
    log_file = "backend.log"
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 3. Telegram Handler (for WARNING and above)
    notifier = TelegramNotifier()
    if notifier.is_configured():
        telegram_handler = TelegramLoggingHandler(notifier)
        telegram_handler.setLevel(logging.WARNING)
        # We use a simpler formatter for Telegram to avoid clutter
        telegram_formatter = logging.Formatter("%(message)s")
        telegram_handler.setFormatter(telegram_formatter)
        logger.addHandler(telegram_handler)

    return logger


# Global logger instance
logger = setup_logger()
