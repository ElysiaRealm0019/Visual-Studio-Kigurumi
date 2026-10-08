import re
from pathlib import Path, PurePosixPath
from typing import Any

from app.core.paths import resolve_repo_path
from app.generation.backends.types import (
    FRONT_OUTPUT_HEIGHT,
    FRONT_OUTPUT_WIDTH,
    TURNAROUND_OUTPUT_HEIGHT,
    TURNAROUND_OUTPUT_WIDTH,
)
from app.generation.modes import expected_output_indexes, is_character_design_mode, is_turnaround_mode

FRONT_PRODUCT_REFERENCE_PATH = "ref/product-reference.png"
TURNAROUND_PRODUCT_REFERENCE_PATH = "ref/turnaround-reference.png"
LANDMARK_KEYS = ("leftEye", "rightEye", "chin", "jawLeft", "jawRight")


def _is_local_revision_payload(prompt_payload: dict[str, Any]) -> bool:
    return str(prompt_payload.get("generation_mode") or "") == "front_local_revision"


def _require_local_edit_payload(prompt_payload: dict[str, Any]) -> dict[str, Any]:
    local_edit = prompt_payload.get("local_edit")
    if not isinstance(local_edit, dict):
        raise RuntimeError("Local revision payload is missing local_edit")
    return local_edit


def _expected_indexes_from_payload(prompt_payload: dict[str, Any]) -> list[int]:
    return expected_output_indexes(str(prompt_payload.get("generation_mode") or "front_design"))


def _output_dimensions_for_mode(generation_mode: str) -> tuple[int, int]:
    if is_turnaround_mode(generation_mode):
        return TURNAROUND_OUTPUT_WIDTH, TURNAROUND_OUTPUT_HEIGHT
    return FRONT_OUTPUT_WIDTH, FRONT_OUTPUT_HEIGHT


def _local_edit_expected_dimensions(
    prompt_payload: dict[str, Any], output_width: int, output_height: int
) -> dict[str, int] | None:
    if not _is_local_revision_payload(prompt_payload):
        return None
    local_edit = _require_local_edit_payload(prompt_payload)
    return {
        "width": int(local_edit.get("base_width") or output_width),
        "height": int(local_edit.get("base_height") or output_height),
    }


def _default_front_landmarks() -> dict[str, dict[str, float]]:
    return {
        "leftEye": {"x": 0.42, "y": 0.42},
        "rightEye": {"x": 0.58, "y": 0.42},
        "chin": {"x": 0.5, "y": 0.7},
        "jawLeft": {"x": 0.39, "y": 0.6},
        "jawRight": {"x": 0.61, "y": 0.6},
    }


def normalize_output_landmarks(
    value: Any, *, width: int, height: int
) -> dict[str, dict[str, float]] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Output landmarks must be an object")

    parsed: dict[str, dict[str, float]] = {}
    for key in LANDMARK_KEYS:
        point = value.get(key)
        if not isinstance(point, dict):
            raise ValueError(f"Output landmark {key} must be an object")
        x = _parse_landmark_coordinate(point.get("x"), size=width)
        y = _parse_landmark_coordinate(point.get("y"), size=height)
        parsed[key] = {"x": x, "y": y}

    if parsed["leftEye"]["x"] > parsed["rightEye"]["x"]:
        parsed["leftEye"], parsed["rightEye"] = parsed["rightEye"], parsed["leftEye"]
    if parsed["jawLeft"]["x"] > parsed["jawRight"]["x"]:
        parsed["jawLeft"], parsed["jawRight"] = parsed["jawRight"], parsed["jawLeft"]

    eye_y = round((parsed["leftEye"]["y"] + parsed["rightEye"]["y"]) / 2, 4)
    jaw_y = round((parsed["jawLeft"]["y"] + parsed["jawRight"]["y"]) / 2, 4)
    parsed["leftEye"]["y"] = eye_y
    parsed["rightEye"]["y"] = eye_y
    parsed["jawLeft"]["y"] = jaw_y
    parsed["jawRight"]["y"] = jaw_y
    return parsed


def _parse_landmark_coordinate(value: Any, *, size: int) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("Output landmark coordinates must be numbers")
    coordinate = float(value)
    if coordinate > 1 and size > 1:
        coordinate = coordinate / size
    return max(0.0, min(1.0, round(coordinate, 4)))


def _resolve_uploaded_reference_path(reference_key: Any, reference_root: Path) -> Path | None:
    if not isinstance(reference_key, str) or not reference_key:
        return None
    if "://" in reference_key or reference_key.startswith("//"):
        return None
    normalized = reference_key.replace("\\", "/")
    if ":" in normalized:
        normalized = normalized.split(":", 1)[1]
    if normalized.startswith("//"):
        return None
    posix_path = PurePosixPath(normalized)
    if (
        posix_path.is_absolute()
        or any(part == ".." for part in posix_path.parts)
        or len(posix_path.parts) < 3
        or posix_path.parts[0] != "references"
    ):
        return None

    try:
        relative_path = Path(*posix_path.parts[1:])
        candidate = (reference_root / relative_path).resolve()
    except OSError:
        return None
    try:
        candidate.relative_to(reference_root.resolve())
    except ValueError:
        return None
    return candidate


def _safe_path_segment(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return sanitized or "unknown"


def _product_reference_paths_for_mode(generation_mode: str, settings: Any) -> list[Path]:
    if is_character_design_mode(generation_mode):
        # Character design sheets are 2D art; finished-product photos would pull them toward the head shell look.
        return []
    configured_references = _resolve_optional_product_reference_paths(settings.codex_product_reference_path)
    if generation_mode == "turnaround":
        candidates = [
            *_resolve_optional_product_reference_paths(TURNAROUND_PRODUCT_REFERENCE_PATH),
            *configured_references,
        ]
    elif any(candidate.is_file() for candidate in configured_references):
        candidates = configured_references
    else:
        candidates = _resolve_optional_product_reference_paths(FRONT_PRODUCT_REFERENCE_PATH)
    paths: list[Path] = []
    for candidate in candidates:
        if candidate.is_file() and candidate not in paths:
            paths.append(candidate)
    return paths


def _resolve_optional_product_reference_paths(value: str | None) -> list[Path]:
    path = _resolve_optional_setting_path(value)
    if path is None:
        return []
    paths = [path]
    raw_path = Path(value or "")
    repo_root = resolve_repo_path("")
    if not raw_path.is_absolute() and len(repo_root.parents) >= 2 and repo_root.parent.name == ".worktrees":
        paths.append(repo_root.parents[1] / raw_path)
    return paths


def _resolve_optional_setting_path(value: str) -> Path | None:
    if not value:
        return None
    return resolve_repo_path(value)


def product_reference_kind(generation_mode: str, product_paths: list[Path]) -> str:
    if not product_paths:
        return "none"
    if generation_mode != "turnaround":
        return "matching"
    turnaround_name = Path(TURNAROUND_PRODUCT_REFERENCE_PATH).name
    if any(path.name == turnaround_name for path in product_paths):
        return "matching"
    return "front_style_only"
