from __future__ import annotations

import re
from pathlib import Path


def _trim(text: str) -> str:
    return text.strip(" \t\r\n")


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def load_rec_dict_from_yml(yml_path: str) -> list[str]:
    path = Path(yml_path)
    if not path.exists():
        raise FileNotFoundError(f"Cannot find rec yml: {yml_path}")

    items: list[str] = []
    in_dict = False
    with path.open("r", encoding="utf-8", errors="ignore") as file:
        for raw_line in file:
            line = raw_line.lstrip("\ufeff")
            stripped = _trim(line)
            if not stripped:
                continue

            if not in_dict:
                if stripped.startswith("character_dict:"):
                    in_dict = True
                continue

            if stripped.startswith("- "):
                parts = re.split(r"\s+-\s+", stripped[2:])
                for part in parts:
                    part = _unquote(_trim(part))
                    if part:
                        items.append(part)
                continue

            if ":" in stripped:
                break

    if not items:
        raise ValueError("Failed to parse character_dict from rec inference.yml")
    return items
