import asyncio
import json
import logging
import os
import shutil
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.generation.backends.analysis import (
    _build_detail_analysis_prompt,
    _build_reference_safety_prompt,
    parse_reference_safety_json,
)
from app.generation.backends.common import _resolve_uploaded_reference_path, _safe_path_segment
from app.generation.backends.types import ImageGenerationProvider, ReferenceRejectedError
from app.generation.detail_analysis import (
    DetailAnalysisProviderRequest,
    DetailAnalysisProviderResult,
    parse_detail_analysis_json,
)
from app.generation.usage import TokenUsage

logger = logging.getLogger("uvicorn.error")
# Read is the only tool the analysis run may use; it is how Claude Code looks at the reference images.
CLAUDE_CODE_ALLOWED_TOOLS = "Read"
# When the backend itself runs inside a Claude Code session (local development), the host session injects
# variables that point the child CLI at the host's own proxy and disable OAuth, so the logged-in account is
# never used. They are only dropped in that nested case; deployments that set ANTHROPIC_* on purpose keep them.
NESTED_SESSION_MARKER = "CLAUDECODE"
NESTED_SESSION_VARIABLES = (
    "CLAUDECODE",
    "CLAUDE_CODE_SIMPLE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_CHILD_SESSION",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
)


@dataclass(frozen=True)
class ClaudeCodeResult:
    text: str
    token_usage: TokenUsage | None
    permission_denials: list[Any]


class ClaudeCodeLLMBackend(ImageGenerationProvider):
    """Runs reference safety checks and detail analysis through the Claude Code CLI.

    Claude models cannot generate images, so this backend only serves the LLM role.
    """

    name = "claude_code"

    async def generate(self, job_id: str, prompt_payload: dict):
        raise RuntimeError("Claude Code cannot generate images; configure a separate IMAGE_PROVIDER")

    async def analyze_reference_details(
        self,
        request: DetailAnalysisProviderRequest,
    ) -> DetailAnalysisProviderResult:
        settings = get_settings()
        safe_analysis_id = _safe_path_segment(request.analysis_id)
        workspace = (
            resolve_repo_path(settings.claude_code_workspace_dir)
            / _safe_path_segment(request.character_session_id)
            / f"detail-analysis-{safe_analysis_id}"
        )
        workspace.mkdir(parents=True, exist_ok=True)
        image_names = _stage_reference_images(request.reference_keys, workspace, settings.reference_upload_dir)

        safety_prompt = _with_image_instructions(_build_reference_safety_prompt(request), image_names)
        (workspace / "reference-safety-prompt.md").write_text(safety_prompt, encoding="utf-8")
        safety = await run_claude_code(
            safety_prompt,
            workspace=workspace,
            label="reference-safety",
            job_id=f"reference-safety-{safe_analysis_id}",
        )
        safety_result = parse_reference_safety_json(safety.text)
        if not safety_result.allowed:
            reason = (
                "reference_adult_explicit"
                if safety_result.reason == "adult_explicit"
                else "reference_unusable"
            )
            raise ReferenceRejectedError(reason, safety_result.message or reason)

        analysis_prompt = _with_image_instructions(_build_detail_analysis_prompt(request), image_names)
        (workspace / "detail-analysis-prompt.md").write_text(analysis_prompt, encoding="utf-8")
        analysis = await run_claude_code(
            analysis_prompt,
            workspace=workspace,
            label="detail-analysis",
            job_id=f"detail-analysis-{safe_analysis_id}",
        )
        try:
            return parse_detail_analysis_json(analysis.text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("Claude Code detail analysis returned invalid JSON") from exc


def _stage_reference_images(reference_keys: list[str], workspace: Path, reference_upload_dir: str) -> list[str]:
    reference_root = resolve_repo_path(reference_upload_dir)
    refs_dir = workspace / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    image_names: list[str] = []
    missing: list[str] = []
    for index, reference_key in enumerate(reference_keys, start=1):
        path = _resolve_uploaded_reference_path(reference_key, reference_root)
        if path is None or not path.is_file():
            missing.append(str(reference_key))
            continue
        name = f"refs/ref-{index}{path.suffix.lower()}"
        shutil.copy2(path, workspace / name)
        image_names.append(name)
    if missing:
        raise RuntimeError("Detail analysis reference image not found: " + ", ".join(missing))
    if not image_names:
        raise RuntimeError("Detail analysis requires at least one uploaded user reference")
    return image_names


def _with_image_instructions(prompt: str, image_names: list[str]) -> str:
    listing = "\n".join(f"- {name}" for name in image_names)
    return "\n".join(
        [
            "The uploaded reference images are files in the current directory. "
            "Use the Read tool to view every one of them before answering:",
            listing,
            "",
            prompt,
        ]
    )


def build_claude_code_command(settings: Any, model: str | None = None) -> list[str]:
    command = [
        _resolve_claude_code_path(settings.claude_code_path),
        "-p",
        "--output-format",
        "json",
        "--tools",
        CLAUDE_CODE_ALLOWED_TOOLS,
        "--allowedTools",
        CLAUDE_CODE_ALLOWED_TOOLS,
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        "--strict-mcp-config",
    ]
    model = str(model or settings.claude_code_model or "").strip()
    if model:
        command.extend(["--model", model])
    return command


async def run_claude_code(
    prompt: str, *, workspace: Path, label: str, job_id: str, model: str | None = None
) -> ClaudeCodeResult:
    settings = get_settings()
    command = build_claude_code_command(settings, model)
    timeout_seconds = float(settings.claude_code_timeout_seconds)
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
            env=claude_code_environment(os.environ),
        )
    except OSError as exc:
        raise RuntimeError(f"Failed to start Claude Code CLI: {exc}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(prompt.encode("utf-8")),
            timeout=timeout_seconds if timeout_seconds > 0 else None,
        )
    except asyncio.TimeoutError as exc:
        logger.error("Claude Code CLI timed out job_id=%s timeout=%.0fs", job_id, timeout_seconds)
        with suppress(ProcessLookupError):
            process.kill()
        with suppress(Exception):
            await asyncio.wait_for(process.wait(), timeout=5)
        raise RuntimeError(f"Claude Code CLI timed out after {timeout_seconds:.0f} seconds") from exc

    (workspace / f"{label}-output.json").write_bytes(stdout or b"")
    (workspace / f"{label}-stderr.log").write_bytes(stderr or b"")
    result = parse_claude_code_output(stdout or b"", stderr or b"", process.returncode)
    if result.permission_denials:
        logger.warning(
            "Claude Code denied tool calls job_id=%s denials=%s",
            job_id,
            json.dumps(result.permission_denials, ensure_ascii=False)[:500],
        )
    return result


def claude_code_environment(base: Mapping[str, str]) -> dict[str, str]:
    environment = dict(base)
    if NESTED_SESSION_MARKER in environment:
        for name in NESTED_SESSION_VARIABLES:
            environment.pop(name, None)
    return environment


def parse_claude_code_output(stdout: bytes, stderr: bytes, return_code: int | None) -> ClaudeCodeResult:
    text = stdout.decode("utf-8", errors="ignore").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict):
        detail = stderr.decode("utf-8", errors="ignore").strip() or text
        raise RuntimeError(
            f"Claude Code CLI failed with exit code {return_code}: {detail[:500] or 'no output'}"
        )

    result_text = str(payload.get("result") or "").strip()
    if payload.get("is_error") or return_code not in (0, None):
        raise RuntimeError(_claude_code_failure_detail(payload, result_text))
    return ClaudeCodeResult(
        text=_strip_code_fence(result_text),
        token_usage=_token_usage_from_payload(payload.get("usage")),
        permission_denials=list(payload.get("permission_denials") or []),
    )


def _claude_code_failure_detail(payload: dict[str, Any], result_text: str) -> str:
    status = payload.get("api_error_status")
    if status == 401 or "not logged in" in result_text.lower():
        return "Claude Code 未登录：请在宿主机完成 claude 登录并挂载 CLAUDE_CODE_CONFIG_DIR。"
    if status == 429:
        return "Claude Code 额度或速率已达上限，请稍后重试。"
    return f"Claude Code CLI failed: {result_text[:500] or payload.get('terminal_reason') or 'unknown error'}"


def _token_usage_from_payload(value: Any) -> TokenUsage | None:
    if not isinstance(value, dict):
        return None
    input_tokens = _int(value.get("input_tokens"))
    cache_read = _int(value.get("cache_read_input_tokens"))
    cache_creation = _int(value.get("cache_creation_input_tokens"))
    output_tokens = _int(value.get("output_tokens"))
    total_input = sum(item or 0 for item in (input_tokens, cache_read, cache_creation))
    usage = TokenUsage(
        input_tokens=total_input if input_tokens is not None else None,
        cached_input_tokens=cache_read,
        output_tokens=output_tokens,
        total_tokens=total_input + (output_tokens or 0) if input_tokens is not None else None,
    )
    return usage if usage.has_values() else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def _resolve_claude_code_path(claude_code_path: str) -> str:
    path = Path(claude_code_path)
    if path.is_absolute():
        return str(path)
    if "/" in claude_code_path or "\\" in claude_code_path:
        return str(resolve_repo_path(claude_code_path))
    return shutil.which(claude_code_path) or claude_code_path
