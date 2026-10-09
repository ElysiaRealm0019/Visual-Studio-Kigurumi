"""File-backed prompt overrides: tune LLM/image prompt texts without restarting the service.

The JSON file (PROMPTS_FILE, default runtime/prompts.json) only needs the keys being changed - every other key
keeps the in-code default. Values are strings, or lists of strings for the constraint lists. A key that is missing,
empty or of the wrong type falls back to the default, so a broken file can never take generation down. The file is
re-read whenever its mtime or size changes: saving it takes effect on the next prompt assembly, no restart needed.
"""

import json
import logging
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.paths import resolve_repo_path

logger = logging.getLogger("uvicorn.error")

_cache: tuple[tuple[int, int], Path, dict[str, Any]] | None = None


def reset_prompt_overrides_cache() -> None:
    global _cache
    _cache = None


def is_overridden(key: str) -> bool:
    value = _overrides().get(key)
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def get_prompt_text(key: str, default: str) -> str:
    value = _overrides().get(key)
    return value if isinstance(value, str) and value.strip() else default


def get_prompt_list(key: str, default: list[str]) -> list[str]:
    value = _overrides().get(key)
    if isinstance(value, list) and value and all(isinstance(item, str) and item.strip() for item in value):
        return list(value)
    return default


def _overrides() -> dict[str, Any]:
    global _cache
    path = resolve_repo_path(get_settings().prompts_file)
    try:
        stat = path.stat()
    except OSError:
        return {}
    stamp = (stat.st_mtime_ns, stat.st_size)
    if _cache is not None and _cache[0] == stamp and _cache[1] == path:
        return _cache[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("must be a JSON object")
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unusable prompts file %s: %s", path, exc)
        data = {}
    _cache = (stamp, path, data)
    return data
