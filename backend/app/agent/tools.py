"""Tools the agent LLM can call. Each tool wraps the existing generation pipeline."""

import asyncio
import logging
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.agent.llm import ToolSpec
from app.agent.store import Conversation, DesignImage, conversation_store
from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.generation.codex_usage import ensure_codex_usage_allows_generation
from app.generation.detail_analysis import (
    DetailAnalysisProviderRequest,
    ensure_user_requirement_feature,
    filter_head_detail_analysis_result,
)
from app.generation.job_store import TERMINAL_STATUSES, job_store
from app.generation.local_edit import (
    LocalEditValidationError,
    save_local_edit_inputs,
    validate_local_edit_images,
)
from app.generation.provider import ReferenceRejectedError, get_generation_provider
from app.generation.queue import generation_queue
from app.generation.schemas import (
    CreateJobRequest,
    DetailFeatureIn,
    DetailLockIn,
    ReferenceDescriptionIn,
    normalize_locale,
)
from app.images.watermark import clean_output_path
from app.prompts.safety import sanitize_user_text

logger = logging.getLogger("uvicorn.error")

JOB_POLL_SECONDS = 1.5
BACKEND_ROOT = Path(__file__).resolve().parents[2]
JOB_WAIT_LIMIT_SECONDS = 45 * 60

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="analyze_references",
        description=(
            "Run the safety check and head-detail analysis on all uploaded character reference images. "
            "Call this once after the user uploads references (and again if they add new ones), before "
            "generating the character design. Returns the detected features (hair, eyes, ears, headwear...)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "focus_note": {
                    "type": "string",
                    "description": "What the user said must be kept or emphasised, in their words. May be empty.",
                }
            },
            "required": [],
        },
    ),
    ToolSpec(
        name="generate_design",
        description=(
            "Stage 1. Draw a clean 2D character design of the head (anime illustration on white, not the physical "
            "head shell) from the character references: either a front view or a four-view sheet. The user can then "
            "edit it by hand and approve it. Costs one image generation and takes 1-3 minutes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "view": {
                    "type": "string",
                    "enum": ["front", "turnaround"],
                    "description": "front = one front view (default); turnaround = front, three-quarter, side and back.",
                },
                "instructions": {
                    "type": "string",
                    "description": "Extra design requirements from the user, written as short descriptive phrases.",
                },
                "base_image_id": {
                    "type": "string",
                    "description": "For view=turnaround: an existing design front view to expand into four views.",
                },
            },
            "required": [],
        },
    ),
    ToolSpec(
        name="revise_design",
        description=(
            "Stage 1. Redraw an existing character design (front or four-view) with change instructions, keeping "
            "everything else. Costs one image generation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "instructions": {"type": "string", "description": "The requested changes, concrete and visual."},
                "base_image_id": {
                    "type": "string",
                    "description": "Design image id to start from. Defaults to the current design.",
                },
            },
            "required": ["instructions"],
        },
    ),
    ToolSpec(
        name="approve_design",
        description=(
            "Mark a character design image as approved by the user. Only call this when the user has explicitly "
            "said in their latest message that they accept that design. Required before generate_front_view."
        ),
        parameters={
            "type": "object",
            "properties": {"image_id": {"type": "string", "description": "Design image id the user approved."}},
            "required": ["image_id"],
        },
    ),
    ToolSpec(
        name="generate_front_view",
        description=(
            "Stage 2. Generate the front view of the physical kigurumi head shell (product photo) from the approved "
            "character design. Costs one image generation and takes 1-3 minutes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "instructions": {
                    "type": "string",
                    "description": "Extra design requirements from the user, written as short descriptive phrases.",
                },
                "skip_design": {
                    "type": "boolean",
                    "description": (
                        "Generate straight from the character references without an approved design. Only when "
                        "the user explicitly asked to skip the design stage."
                    ),
                },
            },
            "required": [],
        },
    ),
    ToolSpec(
        name="revise_front_view",
        description=(
            "Stage 2. Regenerate the head shell front view based on an existing one plus change instructions "
            "(e.g. 'make the eyes rounder', 'shorter bangs'). Costs one image generation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "instructions": {"type": "string", "description": "The requested changes, concrete and visual."},
                "base_image_id": {
                    "type": "string",
                    "description": "Front image id to start from. Defaults to the current front view.",
                },
            },
            "required": ["instructions"],
        },
    ),
    ToolSpec(
        name="approve_front_view",
        description=(
            "Mark a head shell front view as approved by the user. Only call this when the user has explicitly "
            "said in their latest message that they accept that image. Required before generate_turnaround."
        ),
        parameters={
            "type": "object",
            "properties": {"image_id": {"type": "string", "description": "Front image id the user approved."}},
            "required": ["image_id"],
        },
    ),
    ToolSpec(
        name="generate_turnaround",
        description=(
            "Stage 2. Generate the head shell four-view turnaround sheet (front, three-quarter, side, back) from "
            "the approved head shell front view. Costs one image generation and takes 1-3 minutes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "instructions": {
                    "type": "string",
                    "description": "Optional notes for the turnaround, such as back-of-head hair details.",
                }
            },
            "required": [],
        },
    ),
]

GENERATION_TOOLS = {"generate_design", "revise_design", "generate_front_view", "revise_front_view", "generate_turnaround"}
DESIGN_ROLES = {"design", "design_turnaround"}
APPROVED_DESIGN_NOTE = (
    "User-approved 2D character design from stage 1 (it may include the user's manual edits). It is the primary "
    "design source: convert it into the physical head shell while keeping its face, eyes, expression, hairstyle, "
    "colors, and accessories."
)


class ToolError(Exception):
    """A tool failed in a way the LLM should hear about and can recover from."""


@dataclass
class ToolContext:
    conversation: Conversation
    # `progress(value, phase, **extra)` patches the tool event; extra fields (e.g. reasoning_tokens) are merged in.
    progress: Callable[..., Awaitable[None]]
    cancelled: Callable[[], bool]


async def run_tool(name: str, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ToolError(f"Unknown tool {name!r}")
    return await handler(arguments, context)


async def _analyze_references(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    conversation = context.conversation
    state = conversation.state
    if not state.references:
        raise ToolError("No reference images uploaded yet. Ask the user to upload at least one character image.")
    focus_note = sanitize_user_text(str(arguments.get("focus_note") or "")).strip()
    provider = get_generation_provider()
    _ensure_usable_provider(provider)
    analysis_id = str(uuid.uuid4())
    await context.progress(10, "analyzing")
    try:
        result = await provider.analyze_reference_details(
            DetailAnalysisProviderRequest(
                analysis_id=analysis_id,
                character_session_id=state.character_session_id,
                free_text=focus_note,
                locale=normalize_locale(conversation.locale),
                reference_keys=[_reference_key(reference.kind, reference.key) for reference in state.references],
                reference_descriptions=[
                    {"reference_key": _reference_key(reference.kind, reference.key), "description": reference.note}
                    for reference in state.references
                    if reference.note
                ],
            ),
            progress=context.progress,
        )
    except ReferenceRejectedError as exc:
        state.analysis = None
        raise ToolError(f"Reference rejected ({exc.reason}): {exc}") from None
    result = filter_head_detail_analysis_result(result)
    result = ensure_user_requirement_feature(
        result, free_text=focus_note, locale=normalize_locale(conversation.locale)
    )
    features = [feature.model_dump() for feature in result.features]
    state.analysis = {"id": analysis_id, "focus_note": focus_note, "features": features, "warnings": result.warnings}
    return {
        "ok": True,
        "features": [
            {"kind": feature["kind"], "label": feature["label"], "description": feature["description"]}
            for feature in features
        ],
        "warnings": result.warnings,
    }


def _require_character_references(context: ToolContext) -> None:
    state = context.conversation.state
    if not state.references:
        raise ToolError("No reference images uploaded yet.")
    if not any(reference.kind == "front" for reference in state.references):
        raise ToolError("No reference is marked as the main front reference.")


def _detail_lock(context: ToolContext) -> DetailLockIn | None:
    analysis = context.conversation.state.analysis
    if not analysis:
        return None
    return DetailLockIn(
        source_analysis_id=analysis["id"],
        user_note=analysis.get("focus_note") or "",
        features=[
            DetailFeatureIn(
                id=feature["id"],
                kind=feature["kind"],
                label=feature.get("label") or "",
                description=feature.get("description") or "",
            )
            for feature in analysis["features"][:24]
        ],
    )


def _character_reference_keys(context: ToolContext) -> list[str]:
    return [_reference_key(reference.kind, reference.key) for reference in context.conversation.state.references]


async def _generate_design(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    state = context.conversation.state
    _require_character_references(context)
    view = str(arguments.get("view") or "front")
    if view not in {"front", "turnaround"}:
        raise ToolError("view must be 'front' or 'turnaround'.")
    instructions = sanitize_user_text(str(arguments.get("instructions") or "")).strip()
    base = None
    if arguments.get("base_image_id"):
        base = state.image(str(arguments["base_image_id"]))
        if base is None or base.role != "design":
            raise ToolError("base_image_id must be a design front view.")
        if view != "turnaround":
            raise ToolError("base_image_id is only used for view=turnaround; use revise_design to change a design.")
    if base is not None:
        # Expand an existing (possibly hand-edited) front design; raw references only clarify hidden details.
        reference_keys = [_reference_key("design", base.reference_key), *_supplemental_keys(context)]
    else:
        reference_keys = _character_reference_keys(context)
    request = CreateJobRequest(
        character_session_id=state.character_session_id,
        free_text=instructions,
        locale=context.conversation.locale,
        reference_keys=reference_keys,
        detail_lock=_detail_lock(context),
        generation_mode="character_turnaround" if view == "turnaround" else "character_front",
    )
    role = "design_turnaround" if view == "turnaround" else "design"
    image = await _run_generation(request, role=role, instructions=instructions, context=context, parent=base)
    return _image_result(image)


async def _revise_design(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    state = context.conversation.state
    instructions = sanitize_user_text(str(arguments.get("instructions") or "")).strip()
    if not instructions:
        raise ToolError("instructions must describe the change.")
    base = state.image(str(arguments.get("base_image_id") or "") or state.current_design_id)
    if base is None or base.role not in DESIGN_ROLES:
        raise ToolError("No character design to revise. Call generate_design first.")
    if base.role == "design_turnaround":
        reference_keys = [_reference_key("design", base.reference_key)]
        mode = "character_turnaround"
    else:
        reference_keys = [_reference_key("front", base.reference_key)]
        mode = "character_revision"
    request = CreateJobRequest(
        character_session_id=state.character_session_id,
        free_text=instructions,
        locale=context.conversation.locale,
        reference_keys=reference_keys,
        generation_mode=mode,
    )
    image = await _run_generation(request, role=base.role, instructions=instructions, context=context, parent=base)
    return _image_result(image)


async def _approve_design(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    state = context.conversation.state
    image = state.image(str(arguments.get("image_id") or ""))
    if image is None or image.role not in DESIGN_ROLES:
        raise ToolError("Unknown design image id.")
    if state.last_user_seq <= image.created_seq:
        raise ToolError("The user has not responded since this design was shown. Ask them to confirm it first.")
    await approve_design(context.conversation, image)
    return {"ok": True, "approved_design_id": image.id}


async def approve_design(conversation: Conversation, image: DesignImage) -> None:
    conversation.state.approved_design_id = image.id
    conversation.state.current_design_id = image.id
    await conversation_store.emit(conversation, "design_approved", image_id=image.id, url=image.url)


async def _generate_front_view(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    state = context.conversation.state
    instructions = sanitize_user_text(str(arguments.get("instructions") or "")).strip()
    design = state.image(state.approved_design_id)
    if design is None:
        if not arguments.get("skip_design"):
            raise ToolError(
                "No approved character design yet. Generate a design with generate_design and wait for the user to "
                "approve it, or pass skip_design=true only if the user explicitly asked to skip the design stage."
            )
        _require_character_references(context)
        reference_keys = _character_reference_keys(context)
        descriptions = []
    else:
        # The approved design (with the user's manual edits) is the locked source; raw references would pull
        # the result back toward the unedited character, so they are left out.
        design_key = _reference_key("design", design.reference_key)
        reference_keys = [design_key]
        descriptions = [ReferenceDescriptionIn(reference_key=design_key, description=APPROVED_DESIGN_NOTE)]
    request = CreateJobRequest(
        character_session_id=state.character_session_id,
        free_text=instructions,
        locale=context.conversation.locale,
        reference_keys=reference_keys,
        reference_descriptions=descriptions,
        detail_lock=_detail_lock(context),
        generation_mode="front_design",
    )
    image = await _run_generation(request, role="front", instructions=instructions, context=context, parent=design)
    state.current_front_id = image.id
    return _image_result(image)


async def _revise_front_view(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    state = context.conversation.state
    instructions = sanitize_user_text(str(arguments.get("instructions") or "")).strip()
    if not instructions:
        raise ToolError("instructions must describe the change.")
    base = state.image(str(arguments.get("base_image_id") or "") or state.current_front_id)
    if base is None or base.role != "front":
        raise ToolError("No front-view image to revise. Generate one first.")
    request = CreateJobRequest(
        character_session_id=state.character_session_id,
        free_text=instructions,
        locale=context.conversation.locale,
        reference_keys=[_reference_key("front", base.reference_key)],
        generation_mode="front_revision",
    )
    image = await _run_generation(request, role="front", instructions=instructions, context=context, parent=base)
    state.current_front_id = image.id
    return _image_result(image)


async def _approve_front_view(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    state = context.conversation.state
    image = state.image(str(arguments.get("image_id") or ""))
    if image is None or image.role != "front":
        raise ToolError("Unknown front image id.")
    if state.last_user_seq <= image.created_seq:
        # Guard against the model approving on its own: the user must have spoken after seeing the image.
        raise ToolError("The user has not responded since this image was shown. Ask them to confirm it first.")
    state.approved_front_id = image.id
    state.current_front_id = image.id
    await conversation_store.emit(context.conversation, "front_approved", image_id=image.id, url=image.url)
    return {"ok": True, "approved_image_id": image.id}


async def _generate_turnaround(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    state = context.conversation.state
    approved = state.image(state.approved_front_id)
    if approved is None:
        raise ToolError(
            "No approved front view. Show the front view to the user and wait for their approval first."
        )
    instructions = sanitize_user_text(str(arguments.get("instructions") or "")).strip()
    reference_keys = [_reference_key("front", approved.reference_key)]
    design = state.image(state.approved_design_id)
    if design is not None and design.role == "design_turnaround":
        # The approved four-view design shows the sides and back of the hair that the front view cannot.
        reference_keys.append(_reference_key("design_extra", design.reference_key))
    request = CreateJobRequest(
        character_session_id=state.character_session_id,
        free_text=instructions,
        locale=context.conversation.locale,
        reference_keys=reference_keys,
        generation_mode="turnaround",
    )
    image = await _run_generation(request, role="turnaround", instructions=instructions, context=context, parent=approved)
    return _image_result(image)


_HANDLERS: dict[str, Callable[[dict[str, Any], ToolContext], Awaitable[dict[str, Any]]]] = {
    "analyze_references": _analyze_references,
    "generate_design": _generate_design,
    "revise_design": _revise_design,
    "approve_design": _approve_design,
    "generate_front_view": _generate_front_view,
    "revise_front_view": _revise_front_view,
    "approve_front_view": _approve_front_view,
    "generate_turnaround": _generate_turnaround,
}


async def _run_generation(
    request: CreateJobRequest,
    *,
    role: str,
    instructions: str,
    context: ToolContext,
    parent: DesignImage | None = None,
    source: str = "agent",
    prepare_job: Callable[[str], None] | None = None,
) -> DesignImage:
    provider = get_generation_provider()
    _ensure_usable_provider(provider)
    if source == "local_revision" and not provider.supports_local_revision:
        raise ToolError("The configured image backend does not support local regeneration.")
    if provider.uses_codex:
        try:
            await ensure_codex_usage_allows_generation(get_settings())
        except HTTPException as exc:
            raise ToolError(f"Codex usage limit: {exc.detail}") from None
    job = job_store.create(request, provider=provider.name)
    if prepare_job is not None:
        prepare_job(job.id)
    generation_queue.submit_job(job.id, provider)
    current = await _wait_for_job(job.id, context)

    output = current.outputs[0]
    public_path = _generated_file_from_url(output.image_url)
    if public_path is None or not public_path.is_file():
        raise ToolError("Generated image file is missing on disk.")
    clean_path = clean_output_path(public_path)
    return await register_version(
        context.conversation,
        role=role,
        url=output.image_url,
        clean_source=clean_path if clean_path and clean_path.is_file() else public_path,
        width=output.width,
        height=output.height,
        instructions=instructions,
        parent=parent,
        source=source,
        job_id=current.id,
    )


async def _wait_for_job(job_id: str, context: ToolContext) -> Any:
    started = time.monotonic()
    last_signal: tuple[int, str] | None = None
    while True:
        current = job_store.get(job_id)
        if current is None:
            raise ToolError("Generation job disappeared.")
        signal = (int(current.progress or 0), str(current.phase_label or current.status))
        if signal != last_signal:
            last_signal = signal
            await context.progress(*signal)
        if current.status in TERMINAL_STATUSES:
            break
        if context.cancelled():
            raise ToolError("Cancelled by the user.")
        if time.monotonic() - started > JOB_WAIT_LIMIT_SECONDS:
            raise ToolError("Generation timed out.")
        await asyncio.sleep(JOB_POLL_SECONDS)

    if current.status not in {"succeeded", "accepted"} or not current.outputs:
        failure = next(
            (event.message for event in reversed(current.events) if event.type == "failed" and event.message),
            current.status,
        )
        raise ToolError(f"Generation failed: {failure}")
    return current


async def register_version(
    conversation: Conversation,
    *,
    role: str,
    url: str,
    clean_source: Path,
    width: int,
    height: int,
    instructions: str,
    parent: DesignImage | None,
    source: str,
    job_id: str = "",
    recipe: dict[str, Any] | None = None,
) -> DesignImage:
    """Add an image to the project's version history and announce it in the timeline."""
    state = conversation.state
    image_id = f"{role}-{sum(1 for item in state.images if item.role == role) + 1}"
    reference_key = _copy_into_references(conversation, clean_source, image_id)
    image = DesignImage(
        id=image_id,
        role=role,
        job_id=job_id,
        url=url,
        reference_key=reference_key,
        width=width,
        height=height,
        instructions=instructions,
        created_seq=conversation.next_seq,
        parent_id=parent.id if parent else None,
        source=source,
        recipe=recipe,
    )
    state.images.append(image)
    if role == "front":
        state.current_front_id = image.id
    elif role in DESIGN_ROLES:
        state.current_design_id = image.id
    await conversation_store.emit(
        conversation,
        "image",
        image_id=image.id,
        role=role,
        url=image.url,
        width=image.width,
        height=image.height,
        instructions=instructions,
        parent_id=image.parent_id,
        source=source,
    )
    return image


async def run_local_revision(
    context: ToolContext,
    *,
    base: DesignImage,
    mask_bytes: bytes,
    edit_note: str,
    lock_outside: bool,
    reference_keys: list[str],
    base_bytes: bytes | None = None,
) -> DesignImage:
    """Regenerate only the masked area of a version. Triggered from the editor, not by the LLM.

    `base_bytes` is the editor's current export (it may include unsaved deformations); defaults to the version.
    """
    if base_bytes is None:
        base_path = reference_path(base.reference_key)
        if not base_path.is_file():
            raise ToolError("Base image file is missing on disk.")
        base_bytes = base_path.read_bytes()
    try:
        validate_local_edit_images(base_bytes, mask_bytes)
    except LocalEditValidationError as exc:
        raise ToolError(f"Invalid mask: {exc.code}") from None
    note = sanitize_user_text(edit_note).strip()
    state = context.conversation.state
    request = CreateJobRequest(
        character_session_id=state.character_session_id,
        free_text=note,
        locale=context.conversation.locale,
        generation_mode="front_local_revision",
    )

    def prepare(job_id: str) -> None:
        local_root = resolve_repo_path("runtime/local-revisions") / state.character_session_id / job_id
        info = save_local_edit_inputs(base_bytes, mask_bytes, local_root)
        job_store.patch_prompt_payload(
            job_id,
            {
                "reference_keys": reference_keys,
                "reference_descriptions": [],
                "local_edit": {
                    "base_image_path": str(local_root / "base.png"),
                    "mask_image_path": str(local_root / "mask.png"),
                    "edit_note": note,
                    "base_width": info.width,
                    "base_height": info.height,
                    # Narrow feather = hard lock outside the mask; wide feather blends the edit into its surroundings.
                    "feather_radius_px": 3 if lock_outside else 16,
                    "subject": "design" if base.role in DESIGN_ROLES else "head_shell",
                },
            },
        )

    return await _run_generation(
        request,
        role=base.role,
        instructions=note,
        context=context,
        parent=base,
        source="local_revision",
        prepare_job=prepare,
    )


async def run_annotated_revision(
    context: ToolContext,
    *,
    base: DesignImage,
    annotated_bytes: bytes,
    instructions: str,
) -> DesignImage:
    """Regenerate a front view (design or head shell) from an editor export with the user's annotations on it."""
    if base.role not in {"front", "design"}:
        raise ToolError("Annotated regeneration only applies to front views.")
    conversation = context.conversation
    relative = Path("agent") / conversation.id / "annotated" / f"{uuid.uuid4().hex}.png"
    destination = resolve_repo_path(get_settings().reference_upload_dir) / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(annotated_bytes)
    note = sanitize_user_text(instructions).strip()
    request = CreateJobRequest(
        character_session_id=conversation.state.character_session_id,
        free_text=note,
        locale=conversation.locale,
        reference_keys=[_reference_key("front", "references/" + relative.as_posix())],
        generation_mode="character_revision" if base.role == "design" else "front_revision",
    )
    return await _run_generation(request, role=base.role, instructions=note, context=context, parent=base)


def reference_path(reference_key: str) -> Path:
    root = resolve_repo_path(get_settings().reference_upload_dir).resolve()
    path = (root / reference_key.removeprefix("references/")).resolve()
    path.relative_to(root)
    return path


def _copy_into_references(conversation: Conversation, source: Path, image_id: str) -> str:
    """Copy a clean (unwatermarked) image into the reference area so later jobs can attach it."""
    if not source.is_file():
        raise ToolError("Image file is missing on disk.")
    relative = Path("agent") / conversation.id / f"{image_id}{source.suffix.lower() or '.png'}"
    destination = resolve_repo_path(get_settings().reference_upload_dir) / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return "references/" + relative.as_posix()


def _generated_file_from_url(image_url: str) -> Path | None:
    settings = get_settings()
    roots = {
        settings.generated_public_prefix.rstrip("/") + "/": settings.codex_output_dir,
        # Fixture outputs (tests / local smoke runs) are served from the static fixtures directory.
        "/api/static/fixtures/": settings.fixture_dir,
    }
    prefix = next((candidate for candidate in roots if image_url.startswith(candidate)), None)
    if prefix is None:
        return None
    relative = image_url[len(prefix) :].split("?", 1)[0]
    root_setting = Path(roots[prefix])
    if prefix == "/api/static/fixtures/" and not root_setting.is_absolute():
        root = (BACKEND_ROOT / root_setting).resolve()  # same base as the static mount in app.main
    else:
        root = resolve_repo_path(roots[prefix]).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def _ensure_usable_provider(provider: Any) -> None:
    if not provider.is_fixture:
        return
    settings = get_settings()
    if settings.app_env.strip().lower() == "production" or not settings.allow_fixture_generation:
        raise ToolError("No real generation backend is configured (GENERATION_PROVIDER / IMAGE_PROVIDER).")


def _supplemental_keys(context: ToolContext) -> list[str]:
    return [_reference_key("supplemental", reference.key) for reference in context.conversation.state.references]


def _reference_key(kind: str, key: str) -> str:
    return f"{kind}:{key}"


def _image_result(image: DesignImage) -> dict[str, Any]:
    return {
        "ok": True,
        "image_id": image.id,
        "role": image.role,
        "size": f"{image.width}x{image.height}",
        "shown_to_user": True,
    }
