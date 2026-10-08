"""Shared pipeline for image backends that call a hosted HTTP image API (SiliconFlow, Volcengine Ark).

Each adapter only builds its request payload and reads the image location from the response; reference
selection, size handling, local-revision compositing, download, storage, and watermarking live here.
"""

import asyncio
import base64
import io
import json
import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.generation.backends.common import (
    _is_local_revision_payload,
    _product_reference_paths_for_mode,
    _require_local_edit_payload,
    _resolve_uploaded_reference_path,
    _safe_path_segment,
    product_reference_kind,
)
from app.generation.backends.image_api_prompt import build_image_prompt, build_local_revision_prompt
from app.generation.backends.types import (
    FRONT_OUTPUT_HEIGHT,
    FRONT_OUTPUT_WIDTH,
    TURNAROUND_OUTPUT_HEIGHT,
    TURNAROUND_OUTPUT_WIDTH,
    ImageGenerationProvider,
    ProviderOutput,
)
from app.generation.local_edit import composite_local_edit
from app.generation.modes import expected_output_indexes, is_turnaround_mode, normalize_generation_mode
from app.images.watermark import apply_kigcraft_watermark

logger = logging.getLogger("uvicorn.error")
RETRY_DELAYS_SECONDS = (5.0, 15.0, 45.0)
CONTACT_SHEET_HEIGHT = 1024
CONTACT_SHEET_GAP = 32
LOCAL_EDIT_MARGIN_RATIO = 0.15
LOCAL_EDIT_MIN_SIDE = 384


class ImageApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReferenceImage:
    kind: str
    path: Path


@dataclass
class PreparedRequest:
    prompt: str
    images: list[Image.Image]
    finalize: Callable[[Path, Path], tuple[int, int]]


@dataclass(frozen=True)
class ApiRequest:
    url: str
    headers: dict[str, str]
    payload: dict[str, Any]


class HttpImageBackend(ImageGenerationProvider):
    """Base class for hosted image APIs. Subclasses describe their settings and request/response format."""

    name = "http_image"
    display_name = "Image API"
    max_reference_images = 3

    @property
    def supports_local_revision(self) -> bool:
        return True

    # --- adapter hooks -------------------------------------------------------------------------------------
    def api_key(self, settings: Any) -> str:
        raise NotImplementedError

    def api_key_env_name(self) -> str:
        raise NotImplementedError

    def workspace_dir(self, settings: Any) -> str:
        raise NotImplementedError

    def timeout_seconds(self, settings: Any) -> float:
        raise NotImplementedError

    def max_retries(self, settings: Any) -> int:
        raise NotImplementedError

    def model_name(self, settings: Any) -> str:
        raise NotImplementedError

    def build_request(
        self, settings: Any, prompt: str, images: list[Image.Image], trace_id: str, generation_mode: str
    ) -> ApiRequest:
        raise NotImplementedError

    def reference_limit(self, settings: Any) -> int:
        return self.max_reference_images

    def retryable_status_codes(self) -> set[int]:
        return {429, 503, 504}

    def trace_id_from_response(self, response: httpx.Response) -> str | None:
        return None

    def extract_image(self, body: Any) -> tuple[str, str]:
        """Return ("url", value) or ("b64", value)."""
        raise NotImplementedError

    # --- shared pipeline -----------------------------------------------------------------------------------
    async def analyze_reference_details(self, request):
        raise RuntimeError(f"{self.display_name} does not analyze references; configure LLM_PROVIDER")

    async def generate(self, job_id: str, prompt_payload: dict) -> list[ProviderOutput]:
        return [item async for item in self.generate_incremental(job_id, prompt_payload)]

    async def generate_incremental(self, job_id: str, prompt_payload: dict):
        settings = get_settings()
        if not self.api_key(settings).strip():
            raise RuntimeError(f"{self.api_key_env_name()} is required when IMAGE_PROVIDER={self.name}")

        session_id = _safe_path_segment(str(prompt_payload.get("character_session_id") or "unknown-session"))
        safe_job_id = _safe_path_segment(job_id)
        workspace = resolve_repo_path(self.workspace_dir(settings)) / session_id / safe_job_id
        (workspace / "outputs").mkdir(parents=True, exist_ok=True)
        public_dir = resolve_repo_path(settings.codex_output_dir) / session_id / safe_job_id
        public_prefix = f"{settings.generated_public_prefix.rstrip('/')}/{session_id}/{safe_job_id}"
        generation_mode = normalize_generation_mode(str(prompt_payload.get("generation_mode") or "front_design"))
        (workspace / "prompt_payload.json").write_text(
            json.dumps(prompt_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if _is_local_revision_payload(prompt_payload):
            prepared = prepare_local_revision(prompt_payload, settings)
        else:
            prepared = prepare_generation(prompt_payload, settings, generation_mode, self.reference_limit(settings))
        (workspace / "prompt.md").write_text(prepared.prompt, encoding="utf-8")

        async with create_http_client(self.timeout_seconds(settings)) as client:
            for index in expected_output_indexes(generation_mode):
                trace_id = f"kigcraft-{safe_job_id}-{index}"
                image_bytes = await self.request_image(
                    client, settings, prepared.prompt, prepared.images, trace_id, generation_mode
                )
                relative_path = Path("outputs") / f"candidate-{index}.webp"
                raw_path = workspace / "outputs" / f"raw-candidate-{index}.png"
                raw_path.write_bytes(image_bytes)
                final_path = workspace / relative_path
                width, height = prepared.finalize(raw_path, final_path)
                destination = public_dir / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(final_path, destination)
                # The platform watermark is turned off in the request, so the explicit AI label is mandatory.
                apply_kigcraft_watermark(destination)
                yield ProviderOutput(
                    index=index,
                    object_key=f"{self.name}/{safe_job_id}/{relative_path.as_posix()}",
                    image_url=f"{public_prefix}/{relative_path.as_posix()}",
                    width=width,
                    height=height,
                )

    async def request_image(
        self,
        client: httpx.AsyncClient,
        settings: Any,
        prompt: str,
        images: list[Image.Image],
        trace_id: str,
        generation_mode: str,
    ) -> bytes:
        api_request = self.build_request(settings, prompt, images, trace_id, generation_mode)
        max_retries = max(0, int(self.max_retries(settings)))
        for attempt in range(max_retries + 1):
            started_at = time.monotonic()
            try:
                response = await client.post(api_request.url, headers=api_request.headers, json=api_request.payload)
            except httpx.ReadTimeout as exc:
                # The server accepted the job but never answered; retrying would multiply the wait without
                # changing the outcome, so fail with a clear message instead.
                raise ImageApiError(
                    f"{self.display_name} did not respond within {self.timeout_seconds(settings):.0f}s "
                    f"(model {self.model_name(settings)}, trace_id={trace_id}); the model may be overloaded"
                ) from exc
            except httpx.TransportError as exc:
                failure = f"{exc.__class__.__name__}: {exc}"
                retryable = True
            else:
                server_trace = self.trace_id_from_response(response) or trace_id
                if response.status_code == 200:
                    kind, value = self._extract(response)
                    logger.info(
                        "%s image generated trace_id=%s duration=%.1fs attempt=%d",
                        self.display_name,
                        server_trace,
                        time.monotonic() - started_at,
                        attempt + 1,
                    )
                    if kind == "b64":
                        return base64.b64decode(value)
                    return await _download(client, value, self.display_name)
                failure = f"HTTP {response.status_code} trace_id={server_trace}: {_error_message(response)}"
                retryable = response.status_code in self.retryable_status_codes()
            if not retryable or attempt >= max_retries:
                raise ImageApiError(f"{self.display_name} image generation failed: {failure}")
            delay = RETRY_DELAYS_SECONDS[min(attempt, len(RETRY_DELAYS_SECONDS) - 1)]
            logger.warning("%s request failed (%s); retrying in %.0fs", self.display_name, failure, delay)
            await asyncio.sleep(delay)
        raise ImageApiError(f"{self.display_name} image generation failed")

    def _extract(self, response: httpx.Response) -> tuple[str, str]:
        try:
            kind, value = self.extract_image(response.json())
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ImageApiError(f"{self.display_name} response did not include an image") from exc
        if not isinstance(value, str) or not value:
            raise ImageApiError(f"{self.display_name} response did not include an image")
        return kind, value


def create_http_client(timeout_seconds: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout_seconds)


async def _download(client: httpx.AsyncClient, url: str, display_name: str) -> bytes:
    # Hosted APIs return short-lived URLs, so the image is fetched immediately.
    try:
        response = await client.get(url)
    except httpx.TransportError as exc:
        raise ImageApiError(f"Failed to download {display_name} image: {exc}") from exc
    if response.status_code != 200:
        raise ImageApiError(f"Failed to download {display_name} image: HTTP {response.status_code}")
    return response.content


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or error)[:300]
        return str(body.get("message") or body)[:300]
    return str(body)[:300]


# --- request preparation -------------------------------------------------------------------------------------


def prepare_generation(
    prompt_payload: dict, settings: Any, generation_mode: str, max_images: int
) -> PreparedRequest:
    references = _user_references(prompt_payload, settings)
    # An approved stage-1 design sheet outranks the raw character references.
    fronts = [reference for reference in references if reference.kind == "design"] or [
        reference for reference in references if reference.kind == "front"
    ]
    if not fronts:
        raise RuntimeError("Image generation requires a front reference image")
    primary = fronts[0]
    is_turnaround = is_turnaround_mode(generation_mode)
    is_design = generation_mode in {"front_design", "character_front"}
    # Revision and turnaround already put the annotations onto the edited front image.
    skipped_kinds = set() if is_design else {"annotation"}
    others = [
        reference
        for reference in references
        if reference is not primary and reference.kind not in skipped_kinds
    ]

    target_ratio = (
        TURNAROUND_OUTPUT_WIDTH / TURNAROUND_OUTPUT_HEIGHT
        if is_turnaround
        else FRONT_OUTPUT_WIDTH / FRONT_OUTPUT_HEIGHT
    )
    images = [pad_to_ratio(load_rgb(primary.path), target_ratio)]
    if primary.kind == "design":
        roles = ["approved_design"]
    elif generation_mode == "character_revision":
        roles = ["design_draft"]
    elif is_design or generation_mode == "character_turnaround":
        roles = ["character"]
    else:
        roles = ["approved_front"]
    product_paths = _product_reference_paths_for_mode(generation_mode, settings)
    product_reference = product_reference_kind(generation_mode, product_paths)
    if product_paths and len(images) < max_images:
        images.append(load_rgb(product_paths[0]))
        if not is_turnaround:
            roles.append("product_style")
        elif product_reference == "matching":
            roles.append("turnaround_style")
        else:
            roles.append("product_style_single_view")

    remaining = max_images - len(images)
    if others and remaining > 0:
        other_images = [load_rgb(reference.path) for reference in others]
        if len(other_images) <= remaining:
            separate, merged = other_images, []
        else:
            separate, merged = other_images[: remaining - 1], other_images[remaining - 1 :]
        for reference, image in zip(others, separate, strict=False):
            images.append(image)
            roles.append("design_sheet" if reference.kind == "design_extra" else "supplemental")
        if merged:
            images.append(merged[0] if len(merged) == 1 else contact_sheet(merged))
            roles.append("supplemental")

    return PreparedRequest(
        prompt=build_image_prompt(prompt_payload, roles, generation_mode, product_reference),
        images=images,
        finalize=_finalize_turnaround if is_turnaround else _finalize_front,
    )


def prepare_local_revision(prompt_payload: dict, settings: Any) -> PreparedRequest:
    local_edit = _require_local_edit_payload(prompt_payload)
    base_path = Path(str(local_edit.get("base_image_path") or ""))
    mask_path = Path(str(local_edit.get("mask_image_path") or ""))
    if not base_path.is_file() or not mask_path.is_file():
        raise RuntimeError("Local revision base or mask image is missing")
    base = load_rgb(base_path)
    with Image.open(mask_path) as mask_image:
        mask_alpha = mask_image.getchannel("A") if mask_image.mode == "RGBA" else mask_image.convert("L")
    crop_box = local_edit_crop_box(mask_alpha, base.size)

    supplemental = [load_rgb(reference.path) for reference in _user_references(prompt_payload, settings)]
    images = [base.crop(crop_box)]
    if supplemental:
        images.append(supplemental[0] if len(supplemental) == 1 else contact_sheet(supplemental))
    feather_value = local_edit.get("feather_radius_px")
    feather_radius_px = 6 if feather_value is None else int(feather_value)

    def finalize(raw_path: Path, final_path: Path) -> tuple[int, int]:
        crop_size = (crop_box[2] - crop_box[0], crop_box[3] - crop_box[1])
        edited_crop = fit_cover(load_rgb(raw_path), crop_size)
        full = base.copy()
        full.paste(edited_crop, crop_box[:2])
        pasted_path = raw_path.with_name(f"pasted-{raw_path.stem}.png")
        full.save(pasted_path, format="PNG")
        info = composite_local_edit(base_path, mask_path, pasted_path, final_path, feather_radius_px=feather_radius_px)
        return info.width, info.height

    edit_note = str(local_edit.get("edit_note") or prompt_payload.get("user_notes") or "")
    return PreparedRequest(
        prompt=build_local_revision_prompt(
            edit_note, has_supplemental=len(images) > 1, subject=str(local_edit.get("subject") or "head_shell")
        ),
        images=images,
        finalize=finalize,
    )


def _finalize_front(raw_path: Path, final_path: Path) -> tuple[int, int]:
    image = fit_cover(load_rgb(raw_path), (FRONT_OUTPUT_WIDTH, FRONT_OUTPUT_HEIGHT))
    image.save(final_path, format="WEBP", quality=95)
    return image.size


def _finalize_turnaround(raw_path: Path, final_path: Path) -> tuple[int, int]:
    # Keep the model's native resolution (no upscaling). Pad to 3:2 with the background colour
    # instead of cropping, because cropping can cut off the outermost view.
    image = load_rgb(raw_path)
    ratio = TURNAROUND_OUTPUT_WIDTH / TURNAROUND_OUTPUT_HEIGHT
    image = pad_to_ratio(image, ratio, fill=edge_color(image))
    if image.width > TURNAROUND_OUTPUT_WIDTH:
        image = image.resize((TURNAROUND_OUTPUT_WIDTH, TURNAROUND_OUTPUT_HEIGHT), Image.Resampling.LANCZOS)
    image.save(final_path, format="WEBP", quality=95)
    return image.size


def _user_references(prompt_payload: dict, settings: Any) -> list[ReferenceImage]:
    reference_root = resolve_repo_path(settings.reference_upload_dir)
    references: list[ReferenceImage] = []
    for reference_key in prompt_payload.get("reference_keys") or []:
        path = _resolve_uploaded_reference_path(reference_key, reference_root)
        if path is None or not path.is_file():
            continue
        kind = str(reference_key).split(":", 1)[0] if ":" in str(reference_key) else "supplemental"
        references.append(ReferenceImage(kind=kind, path=path))
    return references


# --- image helpers -------------------------------------------------------------------------------------------


def load_rgb(path: Path) -> Image.Image:
    with Image.open(path) as source:
        source.load()
        if source.mode in {"RGBA", "LA"} or (source.mode == "P" and "transparency" in source.info):
            rgba = source.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            return background
        return source.convert("RGB")


def pad_to_ratio(
    image: Image.Image, ratio: float, fill: str | tuple[int, int, int] = "white"
) -> Image.Image:
    width, height = image.size
    if abs(width / height - ratio) < 0.01:
        return image
    if width / height < ratio:
        canvas_size = (round(height * ratio), height)
    else:
        canvas_size = (width, round(width / ratio))
    canvas = Image.new("RGB", canvas_size, fill)
    canvas.paste(image, ((canvas_size[0] - width) // 2, (canvas_size[1] - height) // 2))
    return canvas


def edge_color(image: Image.Image) -> tuple[int, int, int]:
    """Median colour of the outer border, used to extend studio backgrounds seamlessly."""
    width, height = image.size
    border = [
        image.crop((0, 0, width, 1)),
        image.crop((0, height - 1, width, height)),
        image.crop((0, 0, 1, height)),
        image.crop((width - 1, 0, width, height)),
    ]
    pixels = [pixel for strip in border for pixel in strip.getdata()]
    return tuple(sorted(channel)[len(channel) // 2] for channel in zip(*pixels))  # type: ignore[return-value]


def fit_cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_width, target_height = size
    scale = max(target_width / image.width, target_height / image.height)
    resized = image.resize(
        (max(target_width, round(image.width * scale)), max(target_height, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - target_width) // 2
    top = (resized.height - target_height) // 2
    return resized.crop((left, top, left + target_width, top + target_height))


def contact_sheet(images: list[Image.Image]) -> Image.Image:
    scaled = [
        image.resize((max(1, round(image.width * CONTACT_SHEET_HEIGHT / image.height)), CONTACT_SHEET_HEIGHT))
        for image in images
    ]
    width = sum(image.width for image in scaled) + CONTACT_SHEET_GAP * (len(scaled) - 1)
    sheet = Image.new("RGB", (width, CONTACT_SHEET_HEIGHT), "white")
    x = 0
    for image in scaled:
        sheet.paste(image, (x, 0))
        x += image.width + CONTACT_SHEET_GAP
    return sheet


def downscale(image: Image.Image, max_side: int) -> Image.Image:
    if max_side <= 0 or max(image.size) <= max_side:
        return image
    scale = max_side / max(image.size)
    return image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS
    )


def to_data_uri(image: Image.Image, max_side: int) -> str:
    buffer = io.BytesIO()
    downscale(image, max_side).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def local_edit_crop_box(mask_alpha: Image.Image, image_size: tuple[int, int]) -> tuple[int, int, int, int]:
    bbox = mask_alpha.point(lambda value: 255 if value > 0 else 0).getbbox()
    if bbox is None:
        raise RuntimeError("Local revision mask is empty")
    image_width, image_height = image_size
    left, top, right, bottom = bbox
    margin_x = max(16, round((right - left) * LOCAL_EDIT_MARGIN_RATIO))
    margin_y = max(16, round((bottom - top) * LOCAL_EDIT_MARGIN_RATIO))
    left, top, right, bottom = left - margin_x, top - margin_y, right + margin_x, bottom + margin_y
    left, right = _grow_span(left, right, LOCAL_EDIT_MIN_SIDE, image_width)
    top, bottom = _grow_span(top, bottom, LOCAL_EDIT_MIN_SIDE, image_height)
    return left, top, right, bottom


def _grow_span(start: int, end: int, min_length: int, limit: int) -> tuple[int, int]:
    length = end - start
    if length < min_length:
        extra = min_length - length
        start -= extra // 2
        end += extra - extra // 2
    if start < 0:
        end -= start
        start = 0
    if end > limit:
        start -= end - limit
        end = limit
    return max(0, start), min(limit, end)
