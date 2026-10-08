"""Settings page: switch the agent LLM, the analysis LLM and the image backend, and fill in their API keys.

Changes are stored in RUNTIME_SETTINGS_PATH and layered over .env by get_settings(). API keys are write-only:
responses only say whether a key is set, where it comes from and its last four characters.
"""

import json
import os
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agent.llm import resolve_agent_api_key
from app.core.config import (
    EDITABLE_SETTINGS,
    SECRET_SETTINGS,
    Settings,
    get_settings,
    load_runtime_overrides,
    runtime_settings_file,
    valid_setting,
)
from app.generation.backends.openai_compatible import resolve_analysis_endpoint
from app.generation.provider import resolve_backend_names

router = APIRouter(prefix="/settings", tags=["settings"])


class SecretOut(BaseModel):
    set: bool
    source: str  # "override" (saved on the settings page), "env" or ""
    hint: str


class SettingsOut(BaseModel):
    writable: bool
    values: dict[str, str]
    secrets: dict[str, SecretOut]
    overridden: list[str]
    options: dict[str, list[str]]
    status: dict[str, bool]


class SettingsUpdate(BaseModel):
    # A null value drops the override, so the .env value applies again.
    values: dict[str, str | None] = Field(default_factory=dict)


def _fixture_allowed(settings: Settings) -> bool:
    return settings.app_env.strip().lower() != "production" and settings.allow_fixture_generation


def _writable(settings: Settings) -> bool:
    return settings.app_env.strip().lower() != "production"


def _secret(settings: Settings, overrides: dict[str, str], key: str) -> SecretOut:
    value = str(getattr(settings, key)).strip()
    if not value:
        return SecretOut(set=False, source="", hint="")
    return SecretOut(
        set=True,
        source="override" if key in overrides else "env",
        hint=f"…{value[-4:]}" if len(value) >= 12 else "",
    )


def _describe(settings: Settings) -> SettingsOut:
    overrides = load_runtime_overrides(settings)
    values = {key: str(getattr(settings, key)) for key in EDITABLE_SETTINGS if key not in SECRET_SETTINGS}
    try:
        values["llm_provider"], values["image_provider"] = resolve_backend_names(settings)
    except ValueError:
        pass
    options = {
        key: [value for value in allowed if value != "fixture" or _fixture_allowed(settings)]
        for key, allowed in EDITABLE_SETTINGS.items()
        if allowed is not None
    }
    status = {
        "codex_cli": shutil.which(settings.codex_path) is not None,
        "claude_cli": shutil.which(settings.claude_code_path) is not None,
        "codex_bridge": bool(settings.codex_bridge_url.strip()),
        "siliconflow_key": bool(settings.siliconflow_api_key.strip()),
        "ark_key": bool(settings.ark_api_key.strip()),
        "agent_llm_key": bool(resolve_agent_api_key(settings)),
        "analysis_llm_key": bool(resolve_analysis_endpoint(settings)[1]),
    }
    return SettingsOut(
        writable=_writable(settings),
        values=values,
        secrets={key: _secret(settings, overrides, key) for key in sorted(SECRET_SETTINGS)},
        overridden=sorted(overrides),
        options=options,
        status=status,
    )


@router.get("", response_model=SettingsOut)
async def read_settings() -> SettingsOut:
    return _describe(get_settings())


@router.put("", response_model=SettingsOut)
async def update_settings(request: SettingsUpdate) -> SettingsOut:
    settings = get_settings()
    if not _writable(settings):
        raise HTTPException(status_code=403, detail="settings_read_only_in_production")
    overrides: dict[str, Any] = load_runtime_overrides(settings)
    for key, value in request.values.items():
        if value is None:
            overrides.pop(key, None)
            continue
        value = value.strip()
        if not valid_setting(key, value) or (value == "fixture" and not _fixture_allowed(settings)):
            raise HTTPException(status_code=400, detail=f"invalid_setting:{key}")
        overrides[key] = value
    path = runtime_settings_file(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    get_settings.cache_clear()
    return _describe(get_settings())


@router.delete("", response_model=SettingsOut)
async def reset_settings() -> SettingsOut:
    settings = get_settings()
    if not _writable(settings):
        raise HTTPException(status_code=403, detail="settings_read_only_in_production")
    runtime_settings_file(settings).unlink(missing_ok=True)
    get_settings.cache_clear()
    return _describe(get_settings())
