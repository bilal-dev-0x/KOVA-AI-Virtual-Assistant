import logging
import os
import re
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")

# A window title can hold a one-time code ("Login Code: 300253 - Gmail"): runs of 5-8 digits are
# hidden in the log. Years, sizes and times have fewer digits and stay readable.
CODE = re.compile(r"(?<!\d)\d{5,8}(?!\d)")


class MaskingFormatter(logging.Formatter):
    def format(self, record):
        return CODE.sub("•••••", super().format(record))


def get_logger():
    """Shared KOVA logger: logs/kova.log, max 1 MB x 3 files. Never log API keys."""
    logger = logging.getLogger("kova")

    if not logger.handlers:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(LOG_DIR, "kova.log"),
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8"
        )
        handler.setFormatter(MaskingFormatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False

    return logger
