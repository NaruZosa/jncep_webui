"""Sets up logging with Loguru, intercepting the default log handler."""

import logging
import sys
from typing import TYPE_CHECKING, cast, override

from loguru import logger

if TYPE_CHECKING:
    from types import FrameType


def setup_logging() -> None:
    """Configure logging using Loguru with file rotation and console output."""
    class InterceptHandler(logging.Handler):
        """Intercept the stdlib logger."""

        @override
        def emit(self, record: logging.LogRecord) -> None:
            """Intercept the stdlib logger."""
            # Get the corresponding Loguru level if it exists
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = str(record.levelno)

            # Find caller from where originated the logged message
            frame, depth = logging.currentframe(), 2
            while frame.f_code.co_filename == logging.__file__:
                frame = cast("FrameType", frame.f_back)
                depth += 1

            logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())
    logger.remove()
    logging.basicConfig(handlers=[InterceptHandler()], level=0)
    _ = logger.add(
        sys.stderr,
        colorize=True,
        format="<level>{time:YYYY-MM-DD HH:mm:ss} [{level}]</level> - {message}",
        level="INFO",
    )
    _ = logger.add(
        "/logs/jncep.log",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
        rotation="50 MB",
        compression="zip",
        retention="1 week",
    )
