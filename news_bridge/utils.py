from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(name: str) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    return logging.getLogger(name)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
