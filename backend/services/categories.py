"""One category vocabulary shared by AI planning, display, search and policy."""

import json
from pathlib import Path


CATEGORIES = json.loads((Path(__file__).resolve().parents[2] / "shared" / "purchase_categories.json").read_text(encoding="utf-8"))


def normalize_category(value: str | None) -> str:
    if value is None:
        return "general"
    value = value.strip().lower()
    for name, definition in CATEGORIES.items():
        if value == name or value in definition["aliases"]:
            return name
    return value


def category_allowed(required: str, actual: str) -> bool:
    required, actual = normalize_category(required), normalize_category(actual)
    return actual in CATEGORIES.get(required, {"includes": [required]})["includes"]
