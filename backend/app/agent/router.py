import asyncio
import json
import shutil
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image
from pydantic import BaseModel, Field

from app.agent.history import HistoryError, delete_reply, prepare_regenerate
from app.agent.runner import agent_runner
from app.agent.store import Conversation, ReferenceImage, conversation_store
from app.agent.tools import (
    ToolContext,
    approve_design,
    reference_path,
    register_version,
    run_annotated_revision,
    run_local_revision,
)
from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.generation.schemas import normalize_locale
from app.images.watermark import apply_kigcraft_watermark
from app.prompts.safety import sanitize_user_text
from app.references.router import _safe_filename, _validate_image_upload

router = APIRouter(prefix="/agent", tags=["agent"])

MAX_MESSAGE_CHARS = 2000
MAX_FILES_PER_MESSAGE = 6
MAX_REFERENCES_PER_CONVERSATION = 8
MAX_VERSION_BYTES = 20 * 1024 * 1024
MAX_RECIPE_CHARS = 200_000
APPROVE_ROLES = {"approve_front": {"front"}, "approve_design": {"design", "design_turnaround"}}
SSE_HEARTBEAT_SECONDS = 15


class CreateConversationRequest(BaseModel):
    locale: str = Field(default="zh-CN", max_length=16)
    title: str = Field(default="", max_length=80)


class UpdateConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class ConversationSummaryOut(BaseModel):
    id: str
    title: str
    created_at: str
    status: str
    thumbnail_url: str | None = None
    version_count: int = 0


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: str
    locale: str
    status: str
    running: bool
    last_seq: int
    events: list[dict[str, Any]]
    state: dict[str, Any]


def _summary(conversation: Conversation) -> ConversationSummaryOut:
    images = conversation.state.images
    thumbnail = images[-1].url if images else (conversation.state.references[0].url if conversation.state.references else None)
    return ConversationSummaryOut(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        status=conversation.status,
        thumbnail_url=thumbnail,
        version_count=len(images),
    )


def _out(conversation: Conversation) -> ConversationOut:
    data = conversation.to_dict()
    return ConversationOut(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        locale=conversation.locale,
        status=conversation.status,
        running=agent_runner.is_running(conversation.id),
        last_seq=conversation.events[-1]["seq"] if conversation.events else 0,
        events=[event for event in conversation.events if event["type"] != "event_patch"],
        state={
            "references": data["state"]["references"],
            "images": data["state"]["images"],
            "current_design_id": conversation.state.current_design_id,
            "approved_design_id": conversation.state.approved_design_id,
            "current_front_id": conversation.state.current_front_id,
            "approved_front_id": conversation.state.approved_front_id,
            "analysis": conversation.state.analysis,
        },
    )


def _require(conversation_id: str) -> Conversation:
    conversation = conversation_store.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation_not_found")
    return conversation


@router.get("/conversations", response_model=list[ConversationSummaryOut])
async def list_conversations() -> list[ConversationSummaryOut]:
    return [_summary(conversation) for conversation in conversation_store.list()]


@router.post("/conversations", response_model=ConversationOut)
async def create_conversation(payload: CreateConversationRequest) -> ConversationOut:
    conversation = conversation_store.create(normalize_locale(payload.locale))
    title = sanitize_user_text(payload.title.strip())[:80]
    if title:
        conversation.title = title
        conversation_store.save(conversation)
    return _out(conversation)


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
async def update_conversation(conversation_id: str, payload: UpdateConversationRequest) -> ConversationOut:
    conversation = _require(conversation_id)
    conversation.title = sanitize_user_text(payload.title.strip())[:80] or conversation.title
    conversation_store.save(conversation)
    return _out(conversation)


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
async def get_conversation(conversation_id: str) -> ConversationOut:
    return _out(_require(conversation_id))


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict[str, bool]:
    _require(conversation_id)
    if agent_runner.is_running(conversation_id):
        raise HTTPException(status_code=409, detail="conversation_running")
    return {"deleted": conversation_store.delete(conversation_id)}


@router.post("/conversations/{conversation_id}/messages", response_model=ConversationOut)
async def post_message(
    conversation_id: str,
    text: str = Form(default=""),
    action: str = Form(default=""),
    image_id: str = Form(default=""),
    files: list[UploadFile] = File(default=[]),
) -> ConversationOut:
    conversation = _require(conversation_id)
    if agent_runner.is_running(conversation_id):
        raise HTTPException(status_code=409, detail="conversation_running")
    text = sanitize_user_text(text.strip())[:MAX_MESSAGE_CHARS]
    if len(files) > MAX_FILES_PER_MESSAGE:
        raise HTTPException(status_code=400, detail="too_many_files")
    if len(conversation.state.references) + len(files) > MAX_REFERENCES_PER_CONVERSATION:
        raise HTTPException(status_code=400, detail="too_many_references")
    if action not in {"", *APPROVE_ROLES}:
        raise HTTPException(status_code=400, detail="unknown_action")
    approved_image = conversation.state.image(image_id) if action else None
    if action and (approved_image is None or approved_image.role not in APPROVE_ROLES[action]):
        raise HTTPException(status_code=400, detail="unknown_image")
    if not text and not files and not action:
        raise HTTPException(status_code=400, detail="empty_message")

    for upload in files:
        _validate_image_upload(upload, _safe_filename(upload.filename))
    attachments = []
    for upload in files:
        # Append one by one so only the first image ever uploaded becomes the main front reference.
        attachment = _save_reference(conversation, upload)
        conversation.state.references.append(attachment)
        attachments.append(attachment)
    if not conversation.title:
        conversation.title = (text or (attachments[0].file_name if attachments else ""))[:40]

    event = await conversation_store.emit(
        conversation,
        "user_message",
        text=text,
        action=action or None,
        image_id=image_id or None,
        attachments=[{"id": item.id, "url": item.url, "kind": item.kind} for item in attachments],
    )
    conversation.state.last_user_seq = event["seq"]
    # A button click is an explicit approval; apply it directly instead of trusting the LLM to do it.
    if action == "approve_front":
        conversation.state.approved_front_id = image_id
        conversation.state.current_front_id = image_id
        await conversation_store.emit(conversation, "front_approved", image_id=image_id)
    elif action == "approve_design" and approved_image is not None:
        await approve_design(conversation, approved_image)

    conversation.llm_messages.append({"role": "user", "content": _llm_user_content(text, attachments, action, image_id)})
    conversation_store.save(conversation)
    agent_runner.start(conversation)
    return _out(conversation)


@router.delete("/conversations/{conversation_id}/messages/{seq}", response_model=ConversationOut)
async def delete_message(conversation_id: str, seq: int) -> ConversationOut:
    """Delete an assistant reply from the timeline and from the model's context."""
    conversation = _require(conversation_id)
    if agent_runner.is_running(conversation_id):
        raise HTTPException(status_code=409, detail="conversation_running")
    try:
        await delete_reply(conversation, seq)
    except HistoryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    conversation_store.save(conversation)
    return _out(conversation)


@router.post("/conversations/{conversation_id}/regenerate", response_model=ConversationOut)
async def regenerate(conversation_id: str) -> ConversationOut:
    """Run the last user turn again: its replies are hidden and the transcript rewinds to that message."""
    conversation = _require(conversation_id)
    if agent_runner.is_running(conversation_id):
        raise HTTPException(status_code=409, detail="conversation_running")
    try:
        await prepare_regenerate(conversation)
    except HistoryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    agent_runner.start(conversation)
    return _out(conversation)


@router.post("/conversations/{conversation_id}/cancel")
async def cancel_run(conversation_id: str) -> dict[str, bool]:
    _require(conversation_id)
    agent_runner.cancel(conversation_id)
    return {"cancelling": agent_runner.is_running(conversation_id)}


@router.get("/conversations/{conversation_id}/events")
async def stream_events(conversation_id: str, request: Request, after: int = 0) -> StreamingResponse:
    conversation = _require(conversation_id)
    last_event_id = request.headers.get("last-event-id")
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))

    async def generate():
        cursor = after
        condition = conversation_store.condition(conversation_id)
        while True:
            if await request.is_disconnected():
                return
            pending = [event for event in conversation.events if event["seq"] > cursor]
            for event in pending:
                cursor = event["seq"]
                yield f"id: {cursor}\nevent: agent\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if pending:
                continue
            try:
                async with condition:
                    await asyncio.wait_for(condition.wait(), timeout=SSE_HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": heartbeat\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/conversations/{conversation_id}/references", response_model=ConversationOut)
async def upload_references(conversation_id: str, files: list[UploadFile] = File(...)) -> ConversationOut:
    """Add reference images without sending a chat message (e.g. from the project explorer)."""
    conversation = _require(conversation_id)
    if len(files) > MAX_FILES_PER_MESSAGE:
        raise HTTPException(status_code=400, detail="too_many_files")
    if len(conversation.state.references) + len(files) > MAX_REFERENCES_PER_CONVERSATION:
        raise HTTPException(status_code=400, detail="too_many_references")
    for upload in files:
        _validate_image_upload(upload, _safe_filename(upload.filename))
    attachments = []
    for upload in files:
        attachment = _save_reference(conversation, upload)
        conversation.state.references.append(attachment)
        attachments.append(attachment)
    await conversation_store.emit(
        conversation,
        "references_added",
        attachments=[{"id": item.id, "url": item.url, "kind": item.kind} for item in attachments],
    )
    conversation.llm_messages.append(
        {"role": "user", "content": _llm_user_content("", attachments, "", "") + " (added from the explorer)"}
    )
    conversation_store.save(conversation)
    return _out(conversation)


@router.get("/conversations/{conversation_id}/versions/{image_id}/source")
async def get_version_source(conversation_id: str, image_id: str) -> FileResponse:
    """The unwatermarked pixels of a version, used as the editor's working image."""
    conversation = _require(conversation_id)
    image = conversation.state.image(image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="version_not_found")
    path = reference_path(image.reference_key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="version_file_missing")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


@router.post("/conversations/{conversation_id}/versions", response_model=ConversationOut)
async def save_manual_version(
    conversation_id: str,
    base_image_id: str = Form(...),
    note: str = Form(default=""),
    recipe: str = Form(default=""),
    image: UploadFile = File(...),
) -> ConversationOut:
    """Store an image edited in the browser editor as a new version derived from `base_image_id`."""
    conversation = _require(conversation_id)
    if agent_runner.is_running(conversation_id):
        raise HTTPException(status_code=409, detail="conversation_running")
    base = conversation.state.image(base_image_id)
    if base is None:
        raise HTTPException(status_code=400, detail="unknown_image")
    content = await image.read()
    if len(content) > MAX_VERSION_BYTES:
        raise HTTPException(status_code=400, detail="image_too_large")
    try:
        with Image.open(BytesIO(content)) as decoded:
            decoded.load()
            width, height = decoded.size
            clean = decoded.convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="image_invalid") from exc
    parsed_recipe = _parse_recipe(recipe)
    note = sanitize_user_text(note.strip())[:500]

    settings = get_settings()
    staging = conversation_store.workspace(conversation_id) / "manual"
    staging.mkdir(parents=True, exist_ok=True)
    clean_path = staging / f"{uuid.uuid4().hex}.png"
    clean.save(clean_path, format="PNG")
    public_relative = Path(conversation.state.character_session_id) / "manual" / f"{uuid.uuid4().hex}.png"
    public_path = resolve_repo_path(settings.codex_output_dir) / public_relative
    public_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(clean_path, public_path)
    apply_kigcraft_watermark(public_path)

    version = await register_version(
        conversation,
        role=base.role,
        url=f"{settings.generated_public_prefix.rstrip('/')}/{public_relative.as_posix()}",
        clean_source=clean_path,
        width=width,
        height=height,
        instructions=note,
        parent=base,
        source="manual",
        recipe=parsed_recipe,
    )
    conversation.llm_messages.append(
        {
            "role": "user",
            "content": f"[editor action] saved a manual edit of {base.id} as {version.id}"
            + (f": {note}" if note else "")
            + ". Use it as the current version.",
        }
    )
    conversation_store.save(conversation)
    return _out(conversation)


@router.post("/conversations/{conversation_id}/local-revision", response_model=ConversationOut)
async def start_local_revision(
    conversation_id: str,
    base_image_id: str = Form(...),
    edit_note: str = Form(...),
    lock_outside: bool = Form(default=False),
    reference_ids: str = Form(default="[]"),
    mask: UploadFile = File(...),
    base_image: UploadFile | None = File(default=None),
    reference_file: UploadFile | None = File(default=None),
) -> ConversationOut:
    """Regenerate only the masked area of a version (the editor's 局部生成 tool)."""
    conversation = _require(conversation_id)
    if agent_runner.is_running(conversation_id):
        raise HTTPException(status_code=409, detail="conversation_running")
    base = conversation.state.image(base_image_id)
    if base is None:
        raise HTTPException(status_code=400, detail="unknown_image")
    if not edit_note.strip():
        raise HTTPException(status_code=400, detail="edit_note_required")
    mask_bytes = await mask.read()
    base_bytes = await base_image.read() if base_image is not None and base_image.filename else None
    if base_bytes is not None and len(base_bytes) > MAX_VERSION_BYTES:
        raise HTTPException(status_code=400, detail="image_too_large")
    try:
        selected = [str(item) for item in json.loads(reference_ids or "[]")][:2]
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=400, detail="reference_ids_invalid") from None
    references = {reference.id: reference for reference in conversation.state.references}
    reference_keys = [f"supplemental:{references[item].key}" for item in selected if item in references]
    if reference_file is not None and reference_file.filename:
        if len(conversation.state.references) >= MAX_REFERENCES_PER_CONVERSATION:
            raise HTTPException(status_code=400, detail="too_many_references")
        _validate_image_upload(reference_file, _safe_filename(reference_file.filename))
        uploaded = _save_reference(conversation, reference_file)
        uploaded.kind = "supplemental"
        conversation.state.references.append(uploaded)
        reference_keys.append(f"supplemental:{uploaded.key}")

    note = sanitize_user_text(edit_note.strip())[:MAX_MESSAGE_CHARS]

    async def action(context: ToolContext) -> dict[str, Any]:
        version = await run_local_revision(
            context,
            base=base,
            mask_bytes=mask_bytes,
            edit_note=note,
            lock_outside=lock_outside,
            reference_keys=reference_keys,
            base_bytes=base_bytes,
        )
        return {"ok": True, "image_id": version.id}

    await conversation_store.emit(
        conversation, "user_message", text=note, action="local_revision", image_id=base.id, attachments=[]
    )
    agent_runner.start_direct(
        conversation,
        "local_revision",
        action,
        note=f"local regeneration of a masked area of {base.id}: {note}",
    )
    return _out(conversation)



@router.post("/conversations/{conversation_id}/annotated-revision", response_model=ConversationOut)
async def start_annotated_revision(
    conversation_id: str,
    base_image_id: str = Form(...),
    instructions: str = Form(default=""),
    image: UploadFile = File(...),
) -> ConversationOut:
    """Regenerate a front view from the editor's annotated export (the 标注 tool's "regenerate")."""
    conversation = _require(conversation_id)
    if agent_runner.is_running(conversation_id):
        raise HTTPException(status_code=409, detail="conversation_running")
    base = conversation.state.image(base_image_id)
    if base is None or base.role not in {"front", "design"}:
        raise HTTPException(status_code=400, detail="unknown_image")
    content = await image.read()
    if len(content) > MAX_VERSION_BYTES:
        raise HTTPException(status_code=400, detail="image_too_large")
    try:
        with Image.open(BytesIO(content)) as decoded:
            decoded.verify()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="image_invalid") from exc
    note = sanitize_user_text(instructions.strip())[:MAX_MESSAGE_CHARS]

    async def action(context: ToolContext) -> dict[str, Any]:
        version = await run_annotated_revision(context, base=base, annotated_bytes=content, instructions=note)
        return {"ok": True, "image_id": version.id}

    await conversation_store.emit(
        conversation, "user_message", text=note, action="annotated_revision", image_id=base.id, attachments=[]
    )
    agent_runner.start_direct(
        conversation,
        "annotated_revision",
        action,
        note=f"regeneration of {base.id} from the user's annotations: {note}",
    )
    return _out(conversation)

def _parse_recipe(raw: str) -> dict[str, Any] | None:
    if not raw.strip():
        return None
    if len(raw) > MAX_RECIPE_CHARS:
        raise HTTPException(status_code=400, detail="recipe_too_large")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="recipe_invalid") from None
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="recipe_invalid")
    return parsed


def _save_reference(conversation: Conversation, upload: UploadFile) -> ReferenceImage:
    file_name = _safe_filename(upload.filename)
    _validate_image_upload(upload, file_name)
    reference_id = f"ref-{len(conversation.state.references) + 1}-{uuid.uuid4().hex[:6]}"
    relative = f"agent/{conversation.id}/uploads/{reference_id}-{file_name}"
    destination = resolve_repo_path(get_settings().reference_upload_dir) / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        shutil.copyfileobj(upload.file, output)
    has_front = any(reference.kind == "front" for reference in conversation.state.references)
    return ReferenceImage(
        id=reference_id,
        key=f"references/{relative}",
        kind="supplemental" if has_front else "front",
        url=f"/api/references/references/{relative}",
        file_name=file_name,
    )


def _llm_user_content(text: str, attachments: list[ReferenceImage], action: str, image_id: str) -> str:
    parts = []
    if attachments:
        parts.append(
            "[uploaded reference images: "
            + ", ".join(f"{item.id} ({item.kind})" for item in attachments)
            + "]"
        )
    if action == "approve_front":
        parts.append(f"[clicked approve on head shell front view {image_id}; it is now approved]")
    elif action == "approve_design":
        parts.append(f"[clicked approve on character design {image_id}; it is now approved]")
    if text:
        parts.append(text)
    return "\n".join(parts)
