"""
Structured JSON logging configuration.
Call setup_logging() once at application startup.
"""
import logging
import sys
from pythonjsonlogger import jsonlogger


def setup_logging(level: str = "None"):
    """
    Configure structured JSON logging for the application.
    All log handlers are replaced with a single JSON formatter.

    Args:
        level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
               Defaults to INFO if not set or invalid.
    """
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove existing handlers to avoid duplicate output
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
