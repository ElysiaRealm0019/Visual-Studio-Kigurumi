"""Album (`/api/album`) endpoints.

Album entries live only in process memory (the module-level ``_album_items``
list): a service restart clears them, and they are not persisted alongside
projects, generation jobs or version history. Uploaded files still land in
``runtime/generated/album/<item_id>/``, but the list will not show them after
a restart.
"""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.images.watermark import apply_kigcraft_watermark

router = APIRouter(prefix="/album", tags=["album"])

ALLOWED_IMAGE_MEDIA_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class AlbumItemIn(BaseModel):
    image_url: str = Field(min_length=1)
    recipe: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class AlbumItemOut(BaseModel):
    id: str
    image_url: str
    created_at: str
    recipe: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class StoredAlbumItem:
    id: str
    image_url: str
    created_at: str
    recipe: dict[str, Any] | None
    metadata: dict[str, Any] | None


_album_items: list[StoredAlbumItem] = []


def clear_album_items() -> None:
    _album_items.clear()


def _item_to_out(item: StoredAlbumItem) -> AlbumItemOut:
    return AlbumItemOut(
        id=item.id,
        image_url=item.image_url,
        created_at=item.created_at,
        recipe=item.recipe,
        metadata=item.metadata,
    )


@router.post("/items", response_model=AlbumItemOut)
async def save_album_item(payload: AlbumItemIn) -> AlbumItemOut:
    """Store a pointer to an already generated image.

    In memory only: the entry is gone after a service restart.
    """
    item = StoredAlbumItem(
        id=str(uuid.uuid4()),
        image_url=payload.image_url,
        created_at=datetime.now(UTC).isoformat(),
        recipe=payload.recipe,
        metadata=payload.metadata,
    )
    _album_items.append(item)
    return _item_to_out(item)


@router.post("/items/file", response_model=AlbumItemOut)
async def save_album_item_file(
    file: UploadFile = File(...),
    recipe: str | None = Form(default=None),
    metadata: str | None = Form(default=None),
) -> AlbumItemOut:
    """Upload an edited image and store it in the album.

    The file is written under ``runtime/generated/album/<item_id>/``, but the
    album entry itself is in memory only and gone after a service restart.
    """
    content_type = file.content_type or ""
    suffix = ALLOWED_IMAGE_MEDIA_TYPES.get(content_type)
    if suffix is None:
        raise HTTPException(status_code=400, detail="Unsupported album image type")

    item_id = str(uuid.uuid4())
    safe_name = _safe_image_filename(file.filename, suffix)
    output_dir = resolve_repo_path(get_settings().codex_output_dir) / "album" / item_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / safe_name
    output_path.write_bytes(await file.read())
    apply_kigcraft_watermark(output_path)

    image_url = f"{_public_generated_prefix()}/album/{item_id}/{safe_name}"
    item = StoredAlbumItem(
        id=item_id,
        image_url=image_url,
        created_at=datetime.now(UTC).isoformat(),
        recipe=_parse_optional_json_object(recipe, "recipe"),
        metadata=_parse_optional_json_object(metadata, "metadata"),
    )
    _album_items.append(item)
    return _item_to_out(item)


@router.get("/items", response_model=list[AlbumItemOut])
async def list_album_items() -> list[AlbumItemOut]:
    """List the album entries saved by this process, newest first.

    In memory only: empty again after a service restart.
    """
    return [_item_to_out(item) for item in reversed(_album_items)]


def _safe_image_filename(filename: str | None, suffix: str) -> str:
    stem = Path(filename or "edited").stem
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-") or "edited"
    return f"{safe_stem}{suffix}"


def _public_generated_prefix() -> str:
    return f"/{get_settings().generated_public_prefix.strip('/')}"


def _parse_optional_json_object(value: str | None, field_name: str) -> dict[str, Any] | None:
    if value is None or value.strip() == "":
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name} JSON") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail=f"{field_name} must be a JSON object")
    return parsed
