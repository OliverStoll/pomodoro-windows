import logging
import sys
import os
from logging.handlers import RotatingFileHandler


format_str = "[%(asctime)s]  %(levelname)s | %(name)s   -   %(message)s"
date_format = "%Y-%m-%d %H:%M:%S"


if os.getenv("IS_CLOUD", False):
    import google.cloud.logging

    client = google.cloud.logging.Client()
    client.get_default_handler()
    client.setup_logging()
    format_str = "%(levelname)s |  %(name)s   -   %(message)s"


class FixedWidthFormatter(logging.Formatter):
    def format(self, record):
        record.levelname = f"{record.levelname[:7]:<7}"
        record.name = f"{record.name[:16]:<16}"
        return super().format(record)


def create_logger(name: str):
    """Creates a logger using a preset format str. Logs to "All"- and {name}.log if APPDATA_DIR"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    log_formatter = FixedWidthFormatter(format_str, datefmt=date_format)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.DEBUG)
    stream_handler.setFormatter(log_formatter)
    logger.addHandler(stream_handler)

    if os.getenv("APPDATA", False) and os.getenv("APPDATA_DIR", False):
        log_folder = f"{os.getenv('APPDATA')}/{os.getenv('APPDATA_DIR')}/logs"
        os.makedirs(log_folder, exist_ok=True)
        log_file_all = f"{log_folder}/All.log"
        log_file = f"{log_folder}/{name}.log"
        file_handler_a = RotatingFileHandler(log_file_all, maxBytes=50 * 1024 * 1024, backupCount=5)
        file_handler_name = RotatingFileHandler(log_file, maxBytes=50 * 1024 * 1024, backupCount=5)
        file_handler_a.setFormatter(log_formatter)
        file_handler_a.setLevel(logging.DEBUG)
        file_handler_name.setFormatter(log_formatter)
        file_handler_name.setLevel(logging.DEBUG)
        logger.addHandler(file_handler_a)
        logger.addHandler(file_handler_name)

    return logger
