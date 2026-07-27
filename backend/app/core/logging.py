import logging
from pathlib import Path

from app.core.config import REPO_ROOT, settings


def configure_logging() -> None:
    log_path = Path(settings.log_file)
    if not log_path.is_absolute():
        log_path = REPO_ROOT / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(log_path), logging.StreamHandler()],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
