"""Start command-line batch processing with application logging configured."""

from .config import LOGS_DIR
from .logging_utils import setup_logging
from .pipeline import run_batch_pipeline


def main() -> None:
    """Configure logging before executing the batch pipeline."""
    setup_logging(LOGS_DIR)
    run_batch_pipeline()


if __name__ == "__main__":
    main()
