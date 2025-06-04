"""Logging configuration for Bodhi SDK"""

import logging
import sys
from typing import Optional


def setup_logger(name: str = "bodhi", level: Optional[int] = None) -> logging.Logger:
    """Configure and return a logger instance for the SDK.

    Args:
        name: The name of the logger (default: 'bodhi')
        level: The logging level (default: INFO)

    Returns:
        logging.Logger: Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.propagate = False  # Prevent log messages from being propagated

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.setLevel(level or logging.INFO)
    return logger


# Create default logger instance
logger = setup_logger()
