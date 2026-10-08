import json
import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    database_url: str = "postgresql+asyncpg://kig:change-me-postgres-password@postgres:5432/kig_preview"
    redis_url: str = "redis://redis:6379/0"
    s3_endpoint_url: str = "http://minio:9000"
    s3_public_endpoint_url: str = "http://localhost:9000"
    s3_access_key_id: str = "kig_minio"
    s3_secret_access_key: str = "change-me-minio-password"
    s3_bucket: str = "kig-preview"
    jwt_secret: str = Field(default="change-me-generate-a-long-random-secret")
    mock_sms_code: str = "000000"
    generation_provider: str = "fixture"
    llm_provider: str = ""
    image_provider: str = ""
    allow_fixture_generation: bool = False
    fixture_dir: str = "app/static/fixtures"
    codex_path: str = "codex"
    codex_detail_analysis_model: str = "gpt-5.5"
    codex_detail_analysis_reasoning_effort: str = "high"
    codex_detail_analysis_timeout_seconds: int = 240
    codex_workspace_dir: str = "runtime/codex"
    codex_output_dir: str = "runtime/generated"
    codex_bridge_url: str = ""
    codex_bridge_token: str = ""
    codex_bridge_timeout_seconds: int = 1800
    codex_usage_min_remaining_percent: int = 25
    codex_usage_command: str = ""
    codex_usage_check_enabled: bool = True
    codex_usage_check_timeout_seconds: int = 12
    codex_product_reference_path: str = "ref/product-reference.png"
    # Make the head-shell front by editing the product photo into the character instead of rendering from the design.
    head_shell_edit_style_photo: bool = False
    claude_code_path: str = "claude"
    claude_code_model: str = ""
    claude_code_workspace_dir: str = "runtime/claude-code"
    claude_code_timeout_seconds: int = 240
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_image_model: str = "Qwen/Qwen-Image-Edit-2509"
    siliconflow_num_inference_steps: int = 0
    siliconflow_cfg: float = 0.0
    siliconflow_negative_prompt: str = (
        "text, watermark, logo, caption, realistic human skin, realistic human eyes, multiple characters"
    )
    siliconflow_timeout_seconds: int = 300
    siliconflow_max_retries: int = 3
    siliconflow_max_input_side: int = 2048
    siliconflow_workspace_dir: str = "runtime/siliconflow"
    ark_api_key: str = ""
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/plan/v3"
    ark_image_model: str = "doubao-seedream-5-0-pro"
    ark_front_size: str = "2K"
    ark_turnaround_size: str = "1920x1280"
    ark_local_edit_size: str = "1K"
    ark_max_reference_images: int = 4
    ark_timeout_seconds: int = 300
    ark_max_retries: int = 2
    ark_max_input_side: int = 2048
    ark_workspace_dir: str = "runtime/ark"
    agent_llm_provider: str = "openai_compatible"
    agent_llm_base_url: str = "https://ark.cn-beijing.volces.com/api/plan/v3"
    agent_llm_api_key: str = ""
    agent_llm_model: str = "doubao-seed-2-0-pro"
    agent_llm_extra_body: str = '{"thinking": {"type": "disabled"}}'
    agent_llm_timeout_seconds: int = 120
    agent_claude_code_model: str = ""
    # LLM_PROVIDER=openai_compatible (reference analysis). Empty values reuse the chat assistant's endpoint.
    analysis_llm_base_url: str = ""
    analysis_llm_api_key: str = ""
    analysis_llm_model: str = ""
    analysis_llm_max_image_side: int = 1536
    agent_max_steps_per_turn: int = 12
    agent_max_generations_per_turn: int = 3
    agent_dir: str = "runtime/agent"
    clean_output_dir: str = "runtime/clean-outputs"
    reference_upload_dir: str = "runtime/references"
    generated_public_prefix: str = "/api/generated"
    generation_audit_db_path: str = "runtime/generation_audit.sqlite3"
    generation_parallelism: int = 8
    quota_window_hours: int = 5
    mock_output_dir: str = "runtime/mock-outputs"
    max_provider_concurrency: int = 8
    normal_quota_window_hours: int = 5
    normal_base_quota: int = 2
    admin_audit_enabled: bool = True
    admin_audit_password: str = "change-me-admin-audit-password"
    admin_audit_session_hours: int = 12
    admin_audit_max_login_attempts: int = 5
    admin_audit_retry_window_minutes: int = 15
    cors_allowed_origins: str = ""
    trusted_proxy_hosts: str = "127.0.0.1,::1"
    generation_create_rate_limit_window_seconds: int = 300
    generation_create_rate_limit_max_requests: int = 3
    watermark_text: str = "V.S.K AI generated"
    watermark_domain_text: str = "V.S.K"
    # Values changed on the settings page; they override .env until reset.
    runtime_settings_path: str = "runtime/settings-overrides.json"


# Fields the settings page may change, with their allowed values (None = free text).
EDITABLE_SETTINGS: dict[str, tuple[str, ...] | None] = {
    "agent_llm_provider": ("openai_compatible", "claude_code"),
    "agent_llm_base_url": None,
    "agent_llm_model": None,
    "agent_llm_extra_body": None,
    "agent_claude_code_model": None,
    "llm_provider": ("openai_compatible", "codex", "claude_code", "fixture"),
    "analysis_llm_base_url": None,
    "analysis_llm_model": None,
    "image_provider": ("codex", "codex_bridge", "siliconflow", "ark", "fixture"),
    "siliconflow_base_url": None,
    "siliconflow_image_model": None,
    "ark_base_url": None,
    "ark_image_model": None,
    "codex_bridge_url": None,
    "agent_llm_api_key": None,
    "analysis_llm_api_key": None,
    "siliconflow_api_key": None,
    "ark_api_key": None,
    "codex_bridge_token": None,
}
# Write-only fields: the API reports whether they are set, never their value.
SECRET_SETTINGS = frozenset(
    {"agent_llm_api_key", "analysis_llm_api_key", "siliconflow_api_key", "ark_api_key", "codex_bridge_token"}
)
MAX_SETTING_CHARS = 300


def runtime_settings_file(settings: Settings) -> Path:
    from app.core.paths import resolve_repo_path

    return resolve_repo_path(settings.runtime_settings_path)


def valid_setting(key: str, value: object) -> bool:
    if key not in EDITABLE_SETTINGS or not isinstance(value, str) or len(value) > MAX_SETTING_CHARS:
        return False
    if key in SECRET_SETTINGS and (not value.strip() or any(char.isspace() for char in value.strip())):
        return False
    if key == "agent_llm_extra_body":
        from app.core.chat_api import parse_extra_body

        try:
            parse_extra_body(value)
        except ValueError:
            return False
    allowed = EDITABLE_SETTINGS[key]
    return allowed is None or value in allowed


def load_runtime_overrides(settings: Settings) -> dict[str, str]:
    path = runtime_settings_file(settings)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logging.getLogger("uvicorn.error").warning("Ignoring unreadable %s: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {key: value.strip() for key, value in raw.items() if valid_setting(key, value)}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    overrides = load_runtime_overrides(settings)
    return settings.model_copy(update=overrides) if overrides else settings
