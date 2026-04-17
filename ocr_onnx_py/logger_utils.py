from __future__ import annotations

import logging
from pathlib import Path


def setup_file_logger(log_file: str) -> Path:
    path = Path(log_file)
    if not path.is_absolute():
        path = (Path(__file__).resolve().parent / path).resolve()

    path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    return path
