import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


class CustomFormatter(logging.Formatter):
    def format(self, record):
        base_message = super().format(record)

        standard_attrs = logging.LogRecord('', '', '', 0, '', (), None).__dict__.keys()
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in standard_attrs
        }

        if extras:
            extra_str = json.dumps(extras, ensure_ascii=False)
            return f"{base_message} | extra: {extra_str}"
        else:
            return base_message


LOG_DIR = Path("logs")

DATABASE_LOG_FILE = LOG_DIR / "database.log"
API_LOG_FILE = LOG_DIR / "api.log"
CACHE_LOG_FILE = LOG_DIR / "cache.log"

LOG_FORMAT = '%(levelname)s: %(name)s - %(message)s - %(asctime)s'


def setup_logger(name: str, log_file: Path, level: int = logging.INFO, to_console: bool = False) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False  # Не передавать логи родителям

    formatter = CustomFormatter(LOG_FORMAT)

    file_handler = TimedRotatingFileHandler(
        log_file, when="D", interval=1, backupCount=2, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if to_console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


database_logger = setup_logger('database', DATABASE_LOG_FILE)
api_logger = setup_logger('api', API_LOG_FILE)
cache_logger = setup_logger('cache', CACHE_LOG_FILE)
