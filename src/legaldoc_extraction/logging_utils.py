"""Configure shared console and file logging while keeping console colors out of persisted records.

Use module loggers with lazy formatting and a leading [STAGE], followed by an optional [Operation].
Keep progress and usage at INFO, tool reads at DEBUG, recoverable fallbacks at WARNING, and failed work at ERROR with exception details at the catching boundary.
Include document names when concurrent work would otherwise be ambiguous; never log document bodies or credentials.
"""

from copy import copy
import logging
from pathlib import Path
import re

RESET = "\033[0m"
BOLD = "\033[1m"
CONSOLE_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
FILE_LOG_FORMAT = (
    "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s"
)
LOG_FILE_NAME = "legaldoc_extraction.log"

COLORS = {
    # Section tags (message prefixes like "[MAIN] ...")
    "MAIN": "\033[35m",
    "LOADER": "\033[36m",
    "EXTRACTOR": "\033[33m",
    "FIXER": "\033[34m",  # Blue for fixer logs
    "DOCUMENT": "\033[36m",
    "FILES": "\033[34m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
}

FIRST_TAG_RE = re.compile(r"^\[([^\]]+)\]")
TAG_PREFIX_RE = re.compile(r"^(\[[^\]]+\](?:\s+\[[^\]]+\])*)")
TAG_RE = re.compile(r"\[[^\]]+\]")


def _colorize_tag_prefix(message: str) -> str:
    """Colorize a log tag prefix."""
    first_tag_match = FIRST_TAG_RE.match(message)
    if not first_tag_match:
        return message

    color = COLORS.get(first_tag_match.group(1))
    if not color:
        return message

    prefix_match = TAG_PREFIX_RE.match(message)
    if not prefix_match:
        return message

    prefix = prefix_match.group(1)
    colored_prefix = TAG_RE.sub(
        lambda match: f"{BOLD}{color}{match.group(0)}{RESET}", prefix
    )
    return colored_prefix + message[len(prefix) :]


class ColoredFormatter(logging.Formatter):
    """Colored console formatter."""

    def format(self, record: logging.LogRecord) -> str:
        """Colorize a record copy so concurrent handlers retain the original message and exception details."""
        record = copy(record)
        message = _colorize_tag_prefix(record.getMessage())
        levelname = record.levelname
        if levelname in COLORS:
            levelname = f"{COLORS[levelname]}{levelname}{RESET}"

        # The message is already interpolated, so clear arguments on this copy.
        record.msg, record.levelname, record.args = message, levelname, ()
        return super().format(record)


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> logging.Logger:
    """Replace root handlers with console and file handlers, truncating the log file for the new run."""
    # Configure root logger to ensure all child loggers (like legaldoc_extraction.fixer.*) work
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler()
    console.setLevel(level)
    formatter = ColoredFormatter if console.stream.isatty() else logging.Formatter
    console.setFormatter(formatter(CONSOLE_LOG_FORMAT))
    root_logger.addHandler(console)

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / LOG_FILE_NAME
    log_file.write_text("", encoding="utf-8")
    # Append mode follows the current file end after the runner clears a previous run.
    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(FILE_LOG_FORMAT))
    root_logger.addHandler(file_handler)

    # Keep third-party transport logs from flooding the console at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    # Application modules propagate through this namespace to the root handlers.
    logger = logging.getLogger("legaldoc_extraction")
    logger.setLevel(level)

    return logger
