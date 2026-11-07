"""Shared image asset helpers: media types, MIME registration and safe path resolution.

Every route that serves a generated, uploaded or fixture image goes through this module so the
media-type map and the path-traversal checks stay in one place.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

from app.core.paths import resolve_repo_path

IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
IMAGE_SUFFIXES = frozenset(IMAGE_MEDIA_TYPES)
FALLBACK_MEDIA_TYPE = "application/octet-stream"


def register_image_mime_types() -> None:
    """Register image MIME types explicitly instead of trusting the OS `mimetypes` table.

    Windows does not map ``.webp`` by default, which makes ``StaticFiles`` serve WebP fixtures
    as ``text/plain``. Registering before the mounts makes the response deterministic.
    """
    for suffix, media_type in IMAGE_MEDIA_TYPES.items():
        mimetypes.add_type(media_type, suffix)


def image_media_type(path: str | Path) -> str:
    return IMAGE_MEDIA_TYPES.get(Path(path).suffix.lower(), FALLBACK_MEDIA_TYPE)


def is_image_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_MEDIA_TYPES


def resolve_image_asset(
    raw_path: str,
    root: str | Path,
    *,
    required_prefix: str | None = None,
    min_parts: int = 1,
    invalid_status_code: int = 404,
    invalid_detail: str = "Image asset not found",
    unsupported_detail: str | None = None,
    missing_status_code: int = 404,
    missing_detail: str = "Image asset not found",
) -> Path:
    """Resolve a request path to an image file inside ``root``.

    Rejects absolute paths, ``..`` segments, drive/stream prefixes, non-image suffixes and
    paths that escape ``root``. ``required_prefix`` keeps routes whose URLs must start with a
    fixed segment (``references/...``) working, and ``min_parts`` counts that segment too.
    """
    root_dir = _resolved_root(root)
    posix_path = PurePosixPath(raw_path.replace("\\", "/"))
    parts = posix_path.parts
    if (
        not parts
        or len(parts) < min_parts
        or posix_path.is_absolute()
        or any(part in {"", ".", ".."} or ":" in part for part in parts)
        or (required_prefix is not None and parts[0] != required_prefix)
    ):
        raise HTTPException(status_code=invalid_status_code, detail=invalid_detail)

    relative_parts = parts[1:] if required_prefix is not None else parts
    if not is_image_path(relative_parts[-1]):
        raise HTTPException(
            status_code=invalid_status_code,
            detail=unsupported_detail or invalid_detail,
        )

    candidate = (root_dir / Path(*relative_parts)).resolve()
    try:
        candidate.relative_to(root_dir)
    except ValueError as exc:
        raise HTTPException(status_code=invalid_status_code, detail=invalid_detail) from exc
    if not candidate.is_file():
        raise HTTPException(status_code=missing_status_code, detail=missing_detail)
    return candidate


def _resolved_root(root: str | Path) -> Path:
    root_path = Path(root)
    if not root_path.is_absolute():
        root_path = resolve_repo_path(str(root_path))
    return root_path.resolve()
