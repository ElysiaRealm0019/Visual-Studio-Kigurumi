import asyncio
import json
import logging
import os
import re
import shutil
import time
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any

from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.generation.backends.analysis import (
    _build_detail_analysis_prompt,
    _build_reference_safety_prompt,
    parse_reference_safety_json,
)
from app.generation.backends.image_api import fit_cover, load_rgb
from app.generation.backends.common import (
    product_reference_kind,
    _product_reference_paths_for_mode,
    _default_front_landmarks,
    _expected_indexes_from_payload,
    _is_local_revision_payload,
    _local_edit_expected_dimensions,
    _output_dimensions_for_mode,
    _require_local_edit_payload,
    _resolve_uploaded_reference_path,
    _safe_path_segment,
    normalize_output_landmarks,
)
from app.generation.backends.prompting import (
    ProductReferenceKind,
    _detail_lock_heading,
    _format_detail_lock_for_prompt,
    _format_prompt_list,
    _format_reference_descriptions,
    _reference_instruction_for_mode,
    _stage_prompt_for_mode,
    _title_for_mode,
    edits_style_photo,
)
from app.generation.backends.types import (
    FRONT_OUTPUT_HEIGHT,
    FRONT_OUTPUT_WIDTH,
    ImageGenerationProvider,
    ProviderOutput,
    ProviderUsage,
    ReferenceProgress,
    ReferenceRejectedError,
)
from app.generation.detail_analysis import (
    DetailAnalysisProviderRequest,
    DetailAnalysisProviderResult,
    parse_detail_analysis_json,
)
from app.generation.local_edit import composite_local_edit
from app.generation.modes import AI_OUTPUT_LANDMARKS_ENABLED, normalize_generation_mode
from app.generation.usage import TokenUsage, extract_token_usage_from_codex_events, parse_token_usage
from app.images.watermark import apply_kigcraft_watermark
from app.prompts.safety import sanitize_user_text

logger = logging.getLogger("uvicorn.error")
CODEX_HEARTBEAT_SECONDS = 60
CODEX_MAX_ATTEMPTS = 2
CODEX_CANDIDATE_POLL_SECONDS = 2.0
CODEX_CANDIDATE_STABLE_SECONDS = 1.0
# Direct Codex runs only: the service collects the tool output itself (see _codex_tool_generated_images).
TOOL_OUTPUT_COLLECTION_NOTE = (
    "If the image generation tool saves the image outside this workspace, do not run commands to copy, "
    "convert, or resize it; the service collects it directly from the tool output. Still write manifest.json."
)
IMAGE_GENERATION_TOOL_REQUIREMENT = (
    "You must use the image generation tool (gpt-image-2) to create the image output. "
    "If the image generation tool is unavailable, blocked, or cannot complete the request, "
    "fail the task and do not create manifest.json or any candidate image. "
    "Do not create, draw, render, approximate, or trace the output using Python, PIL, SVG, "
    "canvas, CSS, vector shapes, screenshots, drawing library code, or any other manual/code-based method."
)

class CodexImageProvider(ImageGenerationProvider):
    name = "codex"

    async def generate(self, job_id: str, prompt_payload: dict) -> list[ProviderOutput]:
        outputs_by_index: dict[int, ProviderOutput] = {}
        async for item in self.generate_incremental(job_id, prompt_payload):
            if isinstance(item, ProviderOutput):
                outputs_by_index[item.index] = item
        return [outputs_by_index[index] for index in sorted(outputs_by_index)]

    async def analyze_reference_details(
        self,
        request: DetailAnalysisProviderRequest,
        progress: ReferenceProgress | None = None,
    ) -> DetailAnalysisProviderResult:
        settings = get_settings()
        safe_session = _safe_path_segment(request.character_session_id)
        safe_analysis_id = _safe_path_segment(request.analysis_id)
        workspace = (
            resolve_repo_path(settings.codex_workspace_dir)
            / safe_session
            / f"detail-analysis-{safe_analysis_id}"
        )
        workspace.mkdir(parents=True, exist_ok=True)
        image_paths: list[Path] = []
        missing_reference_keys: list[str] = []
        reference_root = resolve_repo_path(settings.reference_upload_dir)
        for reference_key in request.reference_keys:
            path = _resolve_uploaded_reference_path(reference_key, reference_root)
            if path is not None and path.is_file():
                image_paths.append(path)
            else:
                missing_reference_keys.append(reference_key)
        if missing_reference_keys:
            raise RuntimeError(
                "Detail analysis reference image not found: "
                + ", ".join(str(key) for key in missing_reference_keys)
            )
        if not image_paths:
            raise RuntimeError("Detail analysis requires at least one uploaded user reference")

        safety_prompt_text = _build_reference_safety_prompt(request)
        (workspace / "reference-safety-prompt.md").write_text(safety_prompt_text, encoding="utf-8")
        safety_command = _build_codex_detail_analysis_command(
            settings,
            workspace,
            image_paths,
            safety_prompt_text,
            output_file=workspace / "reference-safety-last-message.txt",
        )
        safety_process = await asyncio.create_subprocess_exec(
            *safety_command,
            # codex exec appends a piped stdin to the prompt and waits for EOF; never inherit ours.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
        )
        safety_stdout, safety_stderr, _safety_elapsed = await _communicate_with_heartbeat(
            safety_process,
            job_id=f"reference-safety-{safe_analysis_id}",
            workspace=workspace,
            timeout_seconds=float(getattr(settings, "codex_detail_analysis_timeout_seconds", 240)),
        )
        (workspace / "reference-safety-events.jsonl").write_bytes(safety_stdout or b"")
        (workspace / "reference-safety-stderr.log").write_bytes(safety_stderr or b"")
        if safety_process.returncode != 0:
            raise RuntimeError(_codex_failure_detail(safety_stdout or b"", safety_stderr or b""))
        _ensure_codex_events_do_not_use_disallowed_tools(safety_stdout or b"")

        safety_output_text = safety_stdout.decode("utf-8", errors="ignore").strip()
        try:
            safety_result = parse_reference_safety_json(safety_output_text)
        except (json.JSONDecodeError, ValueError) as exc:
            last_message = workspace / "reference-safety-last-message.txt"
            if last_message.is_file():
                safety_result = parse_reference_safety_json(last_message.read_text(encoding="utf-8"))
            else:
                raise RuntimeError("Codex reference safety check returned invalid JSON") from exc
        if not safety_result.allowed:
            reason = (
                "reference_adult_explicit"
                if safety_result.reason == "adult_explicit"
                else "reference_unusable"
            )
            raise ReferenceRejectedError(reason, safety_result.message or reason)

        prompt_text = _build_detail_analysis_prompt(request)
        (workspace / "detail-analysis-prompt.md").write_text(prompt_text, encoding="utf-8")
        command = _build_codex_detail_analysis_command(settings, workspace, image_paths, prompt_text)
        process = await asyncio.create_subprocess_exec(
            *command,
            # codex exec appends a piped stdin to the prompt and waits for EOF; never inherit ours.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
        )
        stdout, stderr, _elapsed = await _communicate_with_heartbeat(
            process,
            job_id=f"detail-analysis-{safe_analysis_id}",
            workspace=workspace,
            timeout_seconds=float(getattr(settings, "codex_detail_analysis_timeout_seconds", 240)),
        )
        (workspace / "detail-analysis-events.jsonl").write_bytes(stdout or b"")
        (workspace / "detail-analysis-stderr.log").write_bytes(stderr or b"")
        if process.returncode != 0:
            raise RuntimeError(_codex_failure_detail(stdout or b"", stderr or b""))
        _ensure_codex_events_do_not_use_disallowed_tools(stdout or b"")

        output_text = stdout.decode("utf-8", errors="ignore").strip()
        try:
            return parse_detail_analysis_json(output_text)
        except (json.JSONDecodeError, ValueError) as exc:
            last_message = workspace / "detail-analysis-last-message.txt"
            if last_message.is_file():
                return parse_detail_analysis_json(last_message.read_text(encoding="utf-8"))
            raise RuntimeError("Codex detail analysis returned invalid JSON") from exc

    async def generate_incremental(self, job_id: str, prompt_payload: dict):
        from app.generation.codex_manifest import parse_codex_manifest

        settings = get_settings()
        character_session_id = _safe_path_segment(
            str(prompt_payload.get("character_session_id") or "unknown-session")
        )
        safe_job_id = _safe_path_segment(job_id)
        workspace = (
            resolve_repo_path(settings.codex_workspace_dir)
            / character_session_id
            / safe_job_id
        )
        outputs_dir = workspace / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        public_output_dir = (
            resolve_repo_path(settings.codex_output_dir)
            / character_session_id
            / safe_job_id
        )
        is_local_revision = _is_local_revision_payload(prompt_payload)

        expected_indexes = _expected_indexes_from_payload(prompt_payload)
        output_width, output_height = _output_dimensions_for_mode(
            normalize_generation_mode(str(prompt_payload.get("generation_mode") or "front_design"))
        )
        generation_mode = normalize_generation_mode(str(prompt_payload.get("generation_mode") or "front_design"))
        prompt_text = _build_codex_prompt(
            prompt_payload,
            product_reference_kind(generation_mode, _product_reference_paths_for_mode(generation_mode, settings)),
            edit_style_photo=True,
        )
        (workspace / "prompt_payload.json").write_text(
            json.dumps(prompt_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (workspace / "prompt.md").write_text(prompt_text, encoding="utf-8")

        image_paths = _codex_image_paths_for_payload(prompt_payload, settings, workspace)
        command = _build_codex_command(
            settings.codex_path, workspace, image_paths, prompt_text
        )
        logger.info(
                "Starting Codex generation job_id=%s session_id=%s workspace=%s images=%d output_dir=%s",
                safe_job_id,
                character_session_id,
                workspace,
                len(image_paths),
                public_output_dir,
        )

        return_code = -1
        for attempt in range(1, CODEX_MAX_ATTEMPTS + 1):
            process: asyncio.subprocess.Process | None = None
            communicate_task: asyncio.Task[tuple[bytes, bytes]] | None = None
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    # codex exec appends a piped stdin to the prompt and waits for EOF; never inherit ours.
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(workspace),
                )
            except OSError as exc:
                logger.exception("Failed to start Codex CLI job_id=%s workspace=%s", safe_job_id, workspace)
                raise RuntimeError(f"Failed to start Codex CLI: {exc}") from exc

            logger.info(
                "Codex CLI process started job_id=%s attempt=%d/%d pid=%s",
                safe_job_id,
                attempt,
                CODEX_MAX_ATTEMPTS,
                process.pid,
            )
            started_at = time.monotonic()
            last_heartbeat_at = started_at
            communicate_task = asyncio.create_task(process.communicate())

            try:
                while True:
                    done, _ = await asyncio.wait(
                        {communicate_task},
                        timeout=CODEX_CANDIDATE_POLL_SECONDS,
                    )

                    if communicate_task in done:
                        stdout, stderr = communicate_task.result()
                        break

                    now = time.monotonic()
                    if now - last_heartbeat_at >= CODEX_HEARTBEAT_SECONDS:
                        logger.info(
                            "Codex CLI still running job_id=%s elapsed=%.1fs workspace=%s",
                            safe_job_id,
                            now - started_at,
                            workspace,
                        )
                        last_heartbeat_at = now
            finally:
                if communicate_task is not None and not communicate_task.done():
                    _terminate_codex_process(process)
                    communicate_task.cancel()
                    try:
                        await communicate_task
                    except asyncio.CancelledError:
                        pass

            elapsed_seconds = time.monotonic() - started_at
            return_code = process.returncode if process.returncode is not None else -1
            (workspace / f"codex-events-attempt-{attempt}.jsonl").write_bytes(stdout or b"")
            (workspace / f"codex-stderr-attempt-{attempt}.log").write_bytes(stderr or b"")
            (workspace / "codex-events.jsonl").write_bytes(stdout or b"")
            (workspace / "codex-stderr.log").write_bytes(stderr or b"")
            logger.info(
                "Codex CLI finished job_id=%s attempt=%d/%d exit_code=%s duration=%.1fs stdout_bytes=%d stderr_bytes=%d",
                safe_job_id,
                attempt,
                CODEX_MAX_ATTEMPTS,
                return_code,
                elapsed_seconds,
                len(stdout or b""),
                len(stderr or b""),
            )

            if return_code == 0:
                break
            if attempt < CODEX_MAX_ATTEMPTS:
                retry_delay_seconds = 5 * attempt
                logger.warning(
                    "Codex CLI failed job_id=%s attempt=%d/%d exit_code=%s; retrying in %ds stderr_log=%s",
                    safe_job_id,
                    attempt,
                    CODEX_MAX_ATTEMPTS,
                    return_code,
                    retry_delay_seconds,
                    workspace / f"codex-stderr-attempt-{attempt}.log",
                )
                await asyncio.sleep(retry_delay_seconds)

        if return_code != 0:
            failure_detail = _codex_failure_detail(stdout or b"", stderr or b"")
            logger.error(
                "Codex CLI failed job_id=%s exit_code=%s detail=%s stderr_log=%s",
                safe_job_id,
                return_code,
                failure_detail,
                workspace / "codex-stderr.log",
            )
            raise RuntimeError(
                failure_detail
                or f"Generation service failed for job {job_id} with exit code {return_code}."
            )

        tool_images = _codex_tool_generated_images(stdout or b"")
        if tool_images:
            # The image provably came from the image generation tool, so helper commands (reading the
            # imagegen skill, converting formats) are harmless; they are only logged.
            _log_codex_commands(stdout or b"", safe_job_id)
        else:
            _ensure_codex_events_do_not_use_disallowed_tools(stdout or b"")

        token_usage = extract_token_usage_from_codex_events(
            (workspace / "codex-events.jsonl").read_bytes()
        )
        if token_usage is not None:
            yield ProviderUsage(token_usage=token_usage)

        public_prefix = f"{settings.generated_public_prefix.rstrip('/')}/{character_session_id}/{safe_job_id}"
        manifest_path = workspace / "manifest.json"
        if tool_images:
            local_edit_dimensions = _local_edit_expected_dimensions(prompt_payload, output_width, output_height)
            target_size = (
                (local_edit_dimensions["width"], local_edit_dimensions["height"])
                if local_edit_dimensions
                else (output_width, output_height)
            )
            outputs = _outputs_from_tool_images(
                tool_images, expected_indexes, workspace, public_prefix, target_size
            )
        else:
            if not manifest_path.is_file():
                logger.error("Codex manifest missing job_id=%s manifest=%s", safe_job_id, manifest_path)
            outputs = parse_codex_manifest(
                manifest_path,
                public_prefix=public_prefix,
                expected_indexes=expected_indexes,
                generation_mode=normalize_generation_mode(
                    str(prompt_payload.get("generation_mode") or "front_design")
                ),
                local_edit_expected=_local_edit_expected_dimensions(prompt_payload, output_width, output_height),
            )
        if is_local_revision:
            outputs = _composite_codex_local_revision_outputs(outputs, prompt_payload, workspace)
        _copy_public_outputs(outputs, workspace, public_output_dir)
        logger.info("Codex generation produced %d outputs job_id=%s", len(outputs), safe_job_id)
        for output in outputs:
            yield output


class CodexBridgeImageProvider(ImageGenerationProvider):
    name = "codex_bridge"

    async def analyze_reference_details(
        self,
        request: DetailAnalysisProviderRequest,
        progress: ReferenceProgress | None = None,
    ) -> DetailAnalysisProviderResult:
        return await CodexImageProvider().analyze_reference_details(request, progress=progress)

    async def generate(self, job_id: str, prompt_payload: dict) -> list[ProviderOutput]:
        outputs: list[ProviderOutput] = []
        async for item in self.generate_incremental(job_id, prompt_payload):
            if isinstance(item, ProviderOutput):
                outputs.append(item)
        return outputs

    async def generate_incremental(self, job_id: str, prompt_payload: dict):
        settings = get_settings()
        character_session_id = _safe_path_segment(
            str(prompt_payload.get("character_session_id") or "unknown-session")
        )
        safe_job_id = _safe_path_segment(job_id)

        for output_index in _expected_indexes_from_payload(prompt_payload):
            prompt_text = _build_codex_candidate_prompt(
                prompt_payload, output_index, _bridge_product_reference_kind(prompt_payload)
            )
            bridge_payload = {
                "job_id": safe_job_id,
                "character_session_id": character_session_id,
                "output_index": output_index,
                "reference_keys": prompt_payload.get("reference_keys") or [],
                "prompt_payload": prompt_payload,
                "prompt_text": prompt_text,
                "generated_public_prefix": settings.generated_public_prefix,
            }
            response_payload = await _post_codex_bridge_candidate(settings, bridge_payload)
            outputs = _parse_codex_bridge_outputs(
                response_payload,
                settings,
                expected_indexes=[output_index],
                generation_mode=normalize_generation_mode(
                    str(prompt_payload.get("generation_mode") or "front_design")
                ),
            )
            token_usage = _parse_codex_bridge_token_usage(response_payload)
            if token_usage is not None:
                yield ProviderUsage(token_usage=token_usage)
            _watermark_generated_output(outputs[0], settings.codex_output_dir)
            yield outputs[0]


def _bridge_product_reference_kind(prompt_payload: dict[str, Any]) -> ProductReferenceKind:
    # tools/codex_bridge.py always attaches the single CODEX_PRODUCT_REFERENCE_PATH front-view photo.
    if normalize_generation_mode(str(prompt_payload.get("generation_mode") or "front_design")) == "turnaround":
        return "front_style_only"
    return "matching"


def _build_codex_prompt(
    prompt_payload: dict[str, Any],
    product_reference: ProductReferenceKind = "matching",
    edit_style_photo: bool = False,
) -> str:
    if _is_local_revision_payload(prompt_payload):
        return _build_codex_local_revision_prompt(prompt_payload, output_index=1)
    generation_mode = normalize_generation_mode(str(prompt_payload.get("generation_mode") or "front_design"))
    return "\n".join(
        [
            _title_for_mode(generation_mode),
            IMAGE_GENERATION_TOOL_REQUIREMENT,
            TOOL_OUTPUT_COLLECTION_NOTE,
            "",
            *_codex_prompt_body(prompt_payload, generation_mode, product_reference, edit_style_photo),
            "",
            "Produce exactly one image. Save it as:",
            "- outputs/candidate-1.webp",
            "",
            "Write manifest.json in the workspace root with this shape:",
            _manifest_json_example([1], generation_mode),
        ]
    )


def _build_codex_candidate_prompt(
    prompt_payload: dict[str, Any],
    output_index: int,
    product_reference: ProductReferenceKind = "matching",
) -> str:
    if _is_local_revision_payload(prompt_payload):
        return _build_codex_local_revision_prompt(prompt_payload, output_index=output_index)
    generation_mode = normalize_generation_mode(str(prompt_payload.get("generation_mode") or "front_design"))
    return "\n".join(
        [
            _title_for_mode(generation_mode),
            IMAGE_GENERATION_TOOL_REQUIREMENT,
            "",
            *_codex_prompt_body(prompt_payload, generation_mode, product_reference, False),
            "",
            f"Produce exactly one image for candidate {output_index}. Save it as:",
            f"- outputs/candidate-{output_index}.webp",
            "",
            "Write manifest.json in the workspace root with this shape:",
            _manifest_json_example([output_index], generation_mode),
        ]
    )


def _codex_prompt_body(
    prompt_payload: dict[str, Any],
    generation_mode: str,
    product_reference: ProductReferenceKind,
    edit_style_photo: bool,
) -> list[str]:
    # Task first, then which attached image is which, then the user's data (as in the upstream V2 stage briefs):
    # a long detail list ahead of the task made the model redraw the 2D design instead of rendering the shell.
    return [
        _reference_instruction_for_mode(generation_mode, product_reference, edit_style_photo),
        "",
        *_stage_prompt_for_mode(generation_mode, product_reference, edit_style_photo),
        "",
        "Non-negotiable constraints:",
        _format_prompt_list(prompt_payload.get("system_constraints") or []),
        "",
        _detail_lock_heading(generation_mode),
        _format_detail_lock_for_prompt(prompt_payload.get("detail_lock")),
        "",
        "Supplemental reference descriptions:",
        _format_reference_descriptions(prompt_payload.get("reference_descriptions") or []),
        "",
        "Composed user requirements:",
        _format_prompt_list(prompt_payload.get("user_requirements") or []),
        "",
        "Composed user notes:",
        str(prompt_payload.get("user_notes") or ""),
    ]


def _build_codex_local_revision_prompt(prompt_payload: dict[str, Any], output_index: int) -> str:
    local_edit = _require_local_edit_payload(prompt_payload)
    width = int(local_edit.get("base_width") or FRONT_OUTPUT_WIDTH)
    height = int(local_edit.get("base_height") or FRONT_OUTPUT_HEIGHT)
    edit_note = sanitize_user_text(str(local_edit.get("edit_note") or prompt_payload.get("user_notes") or ""))
    reference_descriptions = prompt_payload.get("reference_descriptions") or []
    return "\n".join(
        [
            "You are editing one existing V.S.K front-view image locally.",
            IMAGE_GENERATION_TOOL_REQUIREMENT,
            TOOL_OUTPUT_COLLECTION_NOTE,
            "",
            "You must use the image generation tool edit/mask capability.",
            "Use base.png as the first input image.",
            "Use mask.png as the mask image.",
            "Do not generate a new image from scratch.",
            "Edit only the masked region. Do not redesign the whole character.",
            "Reference images are supplemental only and must not reconstruct the full character.",
            f"The output must be exactly {width}x{height}, the same dimensions as base.png.",
            "If image generation tool mask edit is unavailable, fail without writing manifest.json.",
            "",
            "User local edit note:",
            edit_note or "No extra note.",
            "",
            "Supplemental reference descriptions:",
            _format_reference_descriptions(reference_descriptions),
            "",
            f"Produce exactly one local edit image for candidate {output_index}. Save it as:",
            f"- outputs/candidate-{output_index}.webp",
            "",
            "Write manifest.json in the workspace root with this exact local-edit shape:",
            json.dumps(
                {
                    "generation_source": "image_generation_tool",
                    "tool_action": "edit",
                    "base_image": "base.png",
                    "mask_image": "mask.png",
                    "outputs": [
                        {
                            "index": output_index,
                            "path": f"outputs/candidate-{output_index}.webp",
                            "width": width,
                            "height": height,
                        }
                    ],
                },
                separators=(",", ":"),
            ),
        ]
    )


def _manifest_json_example(indexes: list[int], generation_mode: str) -> str:
    width, height = _output_dimensions_for_mode(generation_mode)

    def output_shape(index: int) -> dict[str, Any]:
        output: dict[str, Any] = {
            "index": index,
            "path": f"outputs/candidate-{index}.webp",
            "width": width,
            "height": height,
        }
        if AI_OUTPUT_LANDMARKS_ENABLED and generation_mode in {"front_design", "front_revision"}:
            output["landmarks"] = _default_front_landmarks()
        return output

    return json.dumps(
        {
            "generation_source": "image_generation_tool",
            "outputs": [output_shape(index) for index in indexes]
        },
        separators=(",", ":"),
    )


def _build_codex_command(
    codex_path: str, workspace: Path, image_paths: list[Path], prompt_text: str
) -> list[str]:
    command = [
        _resolve_codex_path(codex_path),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "-C",
        str(workspace),
        "-o",
        str(workspace / "last-message.txt"),
    ]
    for image_path in image_paths:
        command.extend(["--image", str(image_path)])
    command.append("--")
    command.append(prompt_text)
    return command


def _build_codex_detail_analysis_command(
    settings: Any,
    workspace: Path,
    image_paths: list[Path],
    prompt_text: str,
    *,
    output_file: Path | None = None,
) -> list[str]:
    command = [
        _resolve_codex_path(settings.codex_path),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "-C",
        str(workspace),
        "-m",
        str(settings.codex_detail_analysis_model),
        "-o",
        str(output_file or workspace / "detail-analysis-last-message.txt"),
    ]
    for image_path in image_paths:
        command.extend(["--image", str(image_path)])
    effort = str(getattr(settings, "codex_detail_analysis_reasoning_effort", "") or "").strip()
    if effort:
        command.extend(["-c", f"model_reasoning_effort={effort}"])
    command.append("--")
    command.append(prompt_text)
    return command


async def _communicate_with_heartbeat(
    process: asyncio.subprocess.Process,
    *,
    job_id: str,
    workspace: Path,
    timeout_seconds: float | None = None,
) -> tuple[bytes, bytes, float]:
    started_at = time.monotonic()
    communicate_task = asyncio.create_task(process.communicate())

    while True:
        now = time.monotonic()
        wait_seconds = CODEX_HEARTBEAT_SECONDS
        if timeout_seconds is not None and timeout_seconds > 0:
            remaining_seconds = started_at + timeout_seconds - now
            if remaining_seconds <= 0:
                await _timeout_codex_process(process, communicate_task, job_id, workspace, timeout_seconds)
            wait_seconds = min(wait_seconds, remaining_seconds)

        done, _ = await asyncio.wait({communicate_task}, timeout=wait_seconds)
        if communicate_task in done:
            stdout, stderr = communicate_task.result()
            return stdout, stderr, time.monotonic() - started_at

        logger.info(
            "Codex CLI still running job_id=%s elapsed=%.1fs workspace=%s",
            job_id,
            time.monotonic() - started_at,
            workspace,
        )


async def _timeout_codex_process(
    process: asyncio.subprocess.Process,
    communicate_task: asyncio.Task,
    job_id: str,
    workspace: Path,
    timeout_seconds: float,
) -> None:
    logger.error(
        "Codex CLI timed out job_id=%s timeout=%.1fs workspace=%s",
        job_id,
        timeout_seconds,
        workspace,
    )
    _terminate_codex_process(process)
    wait = getattr(process, "wait", None)
    if callable(wait):
        with suppress(Exception):
            await asyncio.wait_for(wait(), timeout=5)
    if not communicate_task.done():
        communicate_task.cancel()
        with suppress(asyncio.CancelledError):
            await communicate_task
    raise RuntimeError(f"Codex CLI timed out after {timeout_seconds:.0f} seconds")


async def _post_codex_bridge_generate(settings: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return await _post_codex_bridge(settings, "/generate", payload)


async def _post_codex_bridge_candidate(settings: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return await _post_codex_bridge(settings, "/generate-candidate", payload)


async def request_codex_bridge_cancel(job_id: str, prompt_payload: dict[str, Any]) -> None:
    settings = get_settings()
    character_session_id = _safe_path_segment(
        str(prompt_payload.get("character_session_id") or "unknown-session")
    )
    safe_job_id = _safe_path_segment(job_id)
    try:
        await _post_codex_bridge(
            settings,
            "/cancel",
            {"job_id": safe_job_id, "character_session_id": character_session_id},
        )
    except RuntimeError:
        return


async def _post_codex_bridge(
    settings: Any, endpoint: str, payload: dict[str, Any]
) -> dict[str, Any]:
    import httpx

    if not settings.codex_bridge_url:
        raise RuntimeError("CODEX_BRIDGE_URL is required when using codex_bridge provider")
    if not settings.codex_bridge_token:
        raise RuntimeError("CODEX_BRIDGE_TOKEN is required when using codex_bridge provider")

    url = f"{settings.codex_bridge_url.rstrip('/')}{endpoint}"
    headers = {"X-Codex-Bridge-Token": settings.codex_bridge_token}
    timeout = httpx.Timeout(float(settings.codex_bridge_timeout_seconds))

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Codex bridge request failed: {exc}") from exc

    if response.status_code >= 400:
        raise RuntimeError(
            f"Codex bridge failed with HTTP {response.status_code}: {response.text}"
        )

    try:
        response_payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Codex bridge returned invalid JSON") from exc
    if not isinstance(response_payload, dict):
        raise RuntimeError("Codex bridge response must be a JSON object")
    return response_payload


def _parse_codex_bridge_outputs(
    response_payload: dict[str, Any],
    settings: Any,
    *,
    expected_indexes: list[int] | None = None,
    generation_mode: str = "front_design",
) -> list[ProviderOutput]:
    expected_indexes = expected_indexes or [1, 2, 3, 4]
    default_width, default_height = _output_dimensions_for_mode(generation_mode)
    require_landmarks = (
        AI_OUTPUT_LANDMARKS_ENABLED
        and generation_mode in {"front_design", "front_revision"}
        and expected_indexes == [1]
    )
    outputs = response_payload.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != len(expected_indexes):
        expected_count = len(expected_indexes)
        raise RuntimeError(f"Codex bridge must return exactly {expected_count} outputs")

    parsed = [
        _parse_codex_bridge_output(
            output,
            settings,
            default_width=default_width,
            default_height=default_height,
            require_landmarks=require_landmarks,
        )
        for output in outputs
    ]
    parsed.sort(key=lambda output: output.index)
    if [output.index for output in parsed] != expected_indexes:
        raise RuntimeError(
            "Codex bridge output indexes must match the requested candidate indexes"
        )
    return parsed


def _parse_codex_bridge_token_usage(response_payload: dict[str, Any]) -> TokenUsage | None:
    return parse_token_usage(
        response_payload.get("token_usage") or response_payload.get("usage")
    )


def _parse_codex_bridge_output(
    output: Any,
    settings: Any,
    *,
    default_width: int,
    default_height: int,
    require_landmarks: bool,
) -> ProviderOutput:
    if not isinstance(output, dict):
        raise RuntimeError("Codex bridge outputs must be objects")

    index = output.get("index")
    object_key = output.get("object_key")
    image_url = output.get("image_url")
    width = output.get("width", default_width)
    height = output.get("height", default_height)

    if not isinstance(index, int) or isinstance(index, bool):
        raise RuntimeError("Codex bridge output index must be an integer")
    if not isinstance(object_key, str) or not object_key:
        raise RuntimeError("Codex bridge output object_key must be a string")
    if not isinstance(image_url, str) or not image_url:
        raise RuntimeError("Codex bridge output image_url must be a string")
    public_prefix = settings.generated_public_prefix.rstrip("/")
    if not image_url.startswith(f"{public_prefix}/"):
        raise RuntimeError("Codex bridge output image_url must use generated public prefix")
    if not isinstance(width, int) or isinstance(width, bool):
        raise RuntimeError("Codex bridge output width must be an integer")
    if not isinstance(height, int) or isinstance(height, bool):
        raise RuntimeError("Codex bridge output height must be an integer")

    landmarks = None
    if AI_OUTPUT_LANDMARKS_ENABLED:
        try:
            landmarks = normalize_output_landmarks(output.get("landmarks"), width=width, height=height)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
    if require_landmarks and landmarks is None:
        raise RuntimeError("Codex bridge front-view outputs must include edit landmarks")

    return ProviderOutput(
        index=index,
        object_key=object_key,
        image_url=image_url,
        width=width,
        height=height,
        landmarks=landmarks,
    )


def _existing_codex_image_paths(prompt_payload: dict[str, Any], settings: Any) -> list[Path]:
    generation_mode = normalize_generation_mode(str(prompt_payload.get("generation_mode") or "front_design"))
    user_reference_paths: list[Path] = []
    for reference_key in prompt_payload.get("reference_keys") or []:
        reference_path = _resolve_uploaded_reference_path(
            reference_key,
            resolve_repo_path(settings.reference_upload_dir),
        )
        if reference_path is not None and reference_path.is_file():
            user_reference_paths.append(reference_path)

    if not user_reference_paths:
        raise RuntimeError("Codex generation requires at least one uploaded user reference")
    # Codex passes images without file names, so the prompt names them by position. The user's images come first and
    # the application's product-style photos last, except for the head-shell front, which edits the style photo.
    product_paths = _product_reference_paths_for_mode(generation_mode, settings)
    if edits_style_photo(generation_mode, product_reference_kind(generation_mode, product_paths)):
        return [*product_paths, *user_reference_paths]
    return [*user_reference_paths, *product_paths]


def _codex_image_paths_for_payload(
    prompt_payload: dict[str, Any], settings: Any, workspace: Path
) -> list[Path]:
    if _is_local_revision_payload(prompt_payload):
        return _existing_codex_local_revision_image_paths(prompt_payload, settings, workspace)
    return _existing_codex_image_paths(prompt_payload, settings)


def _existing_codex_local_revision_image_paths(
    prompt_payload: dict[str, Any], settings: Any, workspace: Path
) -> list[Path]:
    local_edit = _require_local_edit_payload(prompt_payload)
    base_path = Path(str(local_edit.get("base_image_path") or ""))
    mask_path = Path(str(local_edit.get("mask_image_path") or ""))
    if not base_path.is_file() or not mask_path.is_file():
        raise RuntimeError("Local revision base or mask image is missing")

    workspace_base = workspace / "base.png"
    workspace_mask = workspace / "mask.png"
    shutil.copy2(base_path, workspace_base)
    shutil.copy2(mask_path, workspace_mask)

    supplemental_paths: list[Path] = []
    reference_root = resolve_repo_path(settings.reference_upload_dir)
    for reference_key in prompt_payload.get("reference_keys") or []:
        reference_path = _resolve_uploaded_reference_path(reference_key, reference_root)
        if reference_path is not None and reference_path.is_file():
            supplemental_paths.append(reference_path)

    return [workspace_base, workspace_mask, *supplemental_paths]


def _composite_codex_local_revision_outputs(
    outputs: list[ProviderOutput],
    prompt_payload: dict[str, Any],
    workspace: Path,
) -> list[ProviderOutput]:
    local_edit = _require_local_edit_payload(prompt_payload)
    base_path = Path(str(local_edit.get("base_image_path") or ""))
    mask_path = Path(str(local_edit.get("mask_image_path") or ""))
    feather_radius_value = local_edit.get("feather_radius_px")
    feather_radius_px = 6 if feather_radius_value is None else int(feather_radius_value)

    for output in outputs:
        raw_path = workspace / _relative_output_path(output.image_url)
        debug_path = raw_path.with_name(f"raw-{raw_path.name}")
        if raw_path.is_file():
            shutil.copy2(raw_path, debug_path)
        composite_local_edit(
            base_path,
            mask_path,
            raw_path,
            raw_path,
            feather_radius_px=feather_radius_px,
        )

    return outputs






def _resolve_codex_path(codex_path: str) -> str:
    path = Path(codex_path)
    if path.is_absolute():
        return str(path)
    if "/" in codex_path or "\\" in codex_path:
        return str(resolve_repo_path(codex_path))
    candidate = resolve_repo_path(codex_path)
    if candidate.exists():
        return str(candidate)
    return codex_path




def _copy_public_outputs(
    outputs: list[ProviderOutput], workspace: Path, public_output_dir: Path
) -> None:
    public_output_dir.mkdir(parents=True, exist_ok=True)
    for output in outputs:
        relative_path = _relative_output_path(output.image_url)
        source = workspace / relative_path
        destination = public_output_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        apply_kigcraft_watermark(destination)


def _watermark_generated_output(output: ProviderOutput, codex_output_dir: str) -> None:
    try:
        relative_path = _relative_output_path(output.image_url)
    except ValueError:
        return
    image_path = resolve_repo_path(codex_output_dir) / relative_path
    if image_path.is_file():
        apply_kigcraft_watermark(image_path)


def _stable_codex_candidate_outputs(
    *,
    workspace: Path,
    public_output_dir: Path,
    public_prefix: str,
    character_session_id: str,
    safe_job_id: str,
    expected_indexes: list[int],
    output_width: int,
    output_height: int,
    observed_candidates: dict[int, tuple[tuple[int, int], float]],
    emitted_signatures: dict[int, tuple[int, int]],
) -> list[ProviderOutput]:
    outputs: list[ProviderOutput] = []
    now = time.monotonic()
    for index in expected_indexes:
        source = workspace / "outputs" / f"candidate-{index}.webp"
        try:
            stat = source.stat()
        except OSError:
            continue
        if not source.is_file() or stat.st_size <= 0:
            continue

        signature = (stat.st_size, stat.st_mtime_ns)
        previous = observed_candidates.get(index)
        if previous is None or previous[0] != signature:
            observed_candidates[index] = (signature, now)
            continue
        if now - previous[1] < CODEX_CANDIDATE_STABLE_SECONDS:
            continue
        if emitted_signatures.get(index) == signature:
            continue

        output = ProviderOutput(
            index=index,
            object_key=f"codex/{safe_job_id}/outputs/candidate-{index}.webp",
            image_url=(
                f"{public_prefix.rstrip('/')}/{character_session_id}/{safe_job_id}"
                f"/outputs/candidate-{index}.webp"
            ),
            width=output_width,
            height=output_height,
        )
        _copy_public_outputs([output], workspace, public_output_dir)
        emitted_signatures[index] = signature
        outputs.append(output)
    return outputs


def _terminate_codex_process(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    except AttributeError:
        return


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME", "").strip()
    return Path(configured) if configured else Path.home() / ".codex"


def _codex_thread_id(stdout: bytes) -> str | None:
    for line in stdout.decode("utf-8", errors="ignore").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started":
            thread_id = str(event.get("thread_id") or "").strip()
            return thread_id or None
    return None


def _codex_tool_generated_images(stdout: bytes) -> list[Path]:
    """Images the image generation tool saved for this Codex run.

    Recent Codex CLI versions save tool output under CODEX_HOME/generated_images/<thread_id>/. Only the tool
    writes there, so these files cannot have been drawn by the agent with code.
    """
    thread_id = _codex_thread_id(stdout)
    if not thread_id or _safe_path_segment(thread_id) != thread_id:
        return []
    directory = _codex_home() / "generated_images" / thread_id
    if not directory.is_dir():
        return []
    images = [path for path in directory.iterdir() if path.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg"}]
    return sorted(images, key=lambda path: path.stat().st_mtime)


def _outputs_from_tool_images(
    tool_images: list[Path],
    expected_indexes: list[int],
    workspace: Path,
    public_prefix: str,
    target_size: tuple[int, int],
) -> list[ProviderOutput]:
    if len(tool_images) < len(expected_indexes):
        raise RuntimeError(
            f"Codex image generation tool produced {len(tool_images)} image(s); expected {len(expected_indexes)}"
        )
    outputs: list[ProviderOutput] = []
    # The newest images belong to the final answer when the agent retried the tool.
    for index, source in zip(expected_indexes, tool_images[-len(expected_indexes):]):
        relative_path = Path("outputs") / f"candidate-{index}.webp"
        destination = workspace / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        image = fit_cover(load_rgb(source), target_size)
        image.save(destination, format="WEBP", quality=95)
        outputs.append(
            ProviderOutput(
                index=index,
                object_key=f"codex/{workspace.name}/{relative_path.as_posix()}",
                image_url=f"{public_prefix}/{relative_path.as_posix()}",
                width=image.width,
                height=image.height,
            )
        )
    return outputs


def _log_codex_commands(stdout: bytes, job_id: str) -> None:
    for line in stdout.decode("utf-8", errors="ignore").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") if isinstance(event, dict) else None
        if event.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "command_execution":
            logger.info(
                "Codex helper command job_id=%s exit=%s command=%s",
                job_id,
                item.get("exit_code"),
                str(item.get("command") or "")[:300],
            )


def _ensure_codex_events_do_not_use_disallowed_tools(stdout: bytes) -> None:
    for line in stdout.decode("utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "command_execution":
            command = item.get("command")
            suffix = f": {command}" if isinstance(command, str) and command.strip() else ""
            raise RuntimeError(
                "Codex CLI used command execution instead of the image generation tool"
                f"{suffix}"
            )


def _codex_failure_detail(stdout: bytes, stderr: bytes) -> str:
    messages: list[str] = []
    stdout_text = stdout.decode("utf-8", errors="ignore")
    for line in stdout_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        message = event.get("message")
        if isinstance(message, str) and message.strip():
            messages.append(message.strip())
        error = event.get("error")
        if isinstance(error, dict):
            error_message = error.get("message")
            if isinstance(error_message, str) and error_message.strip():
                messages.append(error_message.strip())

    stderr_text = stderr.decode("utf-8", errors="ignore")
    for line in stderr_text.splitlines():
        stripped = line.strip()
        if stripped and stripped != "Reading additional input from stdin...":
            messages.append(stripped)

    for message in messages:
        lowered = message.lower()
        if "usage limit" in lowered:
            retry_match = re.search(r"try again at ([^.]+)", message, flags=re.IGNORECASE)
            retry_suffix = f" 可重试时间：{retry_match.group(1).strip()}。" if retry_match else ""
            return f"生成额度已用完，请稍后重试或补充额度。{retry_suffix}"
        if (
            "401 unauthorized" in lowered
            or "missing bearer" in lowered
            or "basic authentication" in lowered
            or "authentication in header" in lowered
        ):
            return "生成服务未登录或认证未配置，请更新服务器 Codex 登录凭据。"
        if "token_expired" in lowered or "refresh_token" in lowered:
            return "生成服务登录已过期，请更新服务器登录凭据。"
        if "tls handshake" in lowered or "failed to connect" in lowered:
            return "生成服务连接失败，请在网络恢复后重试。"

    return messages[-1] if messages else "生成服务未能生成图像。"


def _relative_output_path(image_url: str) -> Path:
    normalized = image_url.replace("\\", "/")
    marker = "/outputs/"
    marker_index = normalized.find(marker)
    if marker_index < 0:
        raise ValueError("Generated image URL does not include an outputs path")
    return Path(*PurePosixPath(normalized[marker_index + 1 :]).parts)
