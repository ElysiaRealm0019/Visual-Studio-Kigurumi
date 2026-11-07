"""Head-shell parameter registry: pydantic request/response models over blender/params/schema.json.

Validation and the size derivation live in blender/shell_builder/sizing.py (pure Python) and are
loaded by path, so the backend and the Blender build clamp parameters with the same code.
"""
from __future__ import annotations

import importlib.util
import json
import logging
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.paths import resolve_repo_path

logger = logging.getLogger("uvicorn.error")


class BlenderSchemaUnavailable(RuntimeError):
    pass


def scripts_dir() -> Path:
    return resolve_repo_path(get_settings().blender_scripts_dir)


@lru_cache(maxsize=4)
def _load_sizing(path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location("vsk_shell_sizing", path)
    if spec is None or spec.loader is None:
        raise BlenderSchemaUnavailable(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sizing() -> ModuleType:
    path = scripts_dir() / "shell_builder" / "sizing.py"
    if not path.is_file():
        raise BlenderSchemaUnavailable(f"找不到 {path}")
    return _load_sizing(str(path))


def load_schema() -> dict[str, Any]:
    path = scripts_dir() / "params" / "schema.json"
    if not path.is_file():
        raise BlenderSchemaUnavailable(f"找不到 {path}")
    return sizing().load_schema(path)


def load_presets() -> list[dict[str, Any]]:
    current = load_schema()["schema_version"]
    presets = []
    for path in sorted((scripts_dir() / "params" / "presets").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        version = data.get("schema_version")
        if version != current:
            logger.warning("跳过过期预设 %s：schema_version=%s，当前 %s", path.name, version, current)
            continue
        presets.append({
            "id": path.stem,
            "name": data.get("name", path.stem),
            "schema_version": version,
            "params": data.get("params", {}),
        })
    return presets


class ParamSnapshot(BaseModel):
    """One full build input (source plan §4.3): replayable, diffable, stored with the project."""

    schema_version: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    source: Literal["manual", "agent"] = "manual"
    note: str = Field(default="", max_length=500)


class ValidatedParams(BaseModel):
    schema_version: int
    params: dict[str, Any]
    warnings: list[str]
    derived: dict[str, Any]


def validate_snapshot(snapshot: ParamSnapshot) -> ValidatedParams:
    schema = load_schema()
    module = sizing()
    params, warnings = module.validate(schema, snapshot.params)
    warning = module.schema_version_warning(snapshot.schema_version, schema["schema_version"])
    if warning:
        warnings.insert(0, warning)
    derived = module.resolve(schema, params)
    return ValidatedParams(
        schema_version=schema["schema_version"],
        params=params,
        warnings=warnings + derived["notes"],
        derived=derived,
    )
