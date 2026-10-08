import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from PIL import Image, UnidentifiedImageError

from app.core import chat_api
from app.core.chat_api import ReasoningCallback
from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.generation.backends.analysis import (
    _build_detail_analysis_prompt,
    _build_reference_safety_prompt,
    parse_reference_safety_json,
)
from app.generation.backends.common import _resolve_uploaded_reference_path
from app.generation.backends.image_api import to_data_uri
from app.generation.backends.types import ImageGenerationProvider, ReferenceRejectedError
from app.generation.detail_analysis import (
    DetailAnalysisProviderRequest,
    DetailAnalysisProviderResult,
    parse_detail_analysis_json,
)

logger = logging.getLogger("uvicorn.error")

# Progress is only announced every ~500 estimated reasoning tokens: every event patch rewrites the whole
# conversation file, so the stream must not tick per delta.
REASONING_MILESTONE_TOKENS = 500


def resolve_analysis_endpoint(settings: Any) -> tuple[str, str, str]:
    """Base URL, API key and model for analysis; empty values fall back to the chat assistant's endpoint."""
    agent_base = settings.agent_llm_base_url.strip().rstrip("/")
    base_url = settings.analysis_llm_base_url.strip().rstrip("/") or agent_base
    model = settings.analysis_llm_model.strip() or settings.agent_llm_model.strip()
    key = settings.analysis_llm_api_key.strip()
    if not key and base_url == agent_base:
        key = settings.agent_llm_api_key.strip()
    if not key and base_url == settings.ark_base_url.strip().rstrip("/"):
        key = settings.ark_api_key.strip()
    return base_url, key, model


class OpenAICompatibleLLMBackend(ImageGenerationProvider):
    """Runs reference safety checks and detail analysis through an OpenAI-compatible chat API with vision.

    The model must accept image_url content parts; this backend only serves the LLM role.
    """

    name = "openai_compatible"

    async def generate(self, job_id: str, prompt_payload: dict):
        raise RuntimeError("The OpenAI-compatible analysis backend cannot generate images; configure IMAGE_PROVIDER")

    async def analyze_reference_details(
        self,
        request: DetailAnalysisProviderRequest,
        progress: Callable[[int, str], Awaitable[None]] | None = None,
    ) -> DetailAnalysisProviderResult:
        images = _encode_reference_images(request.reference_keys)
        last_milestone = 0

        async def on_reasoning(estimated_tokens: int) -> None:
            nonlocal last_milestone
            if progress is None or estimated_tokens < last_milestone + REASONING_MILESTONE_TOKENS:
                return
            last_milestone = estimated_tokens
            await progress(10, "analyzing", reasoning_tokens=estimated_tokens)

        safety_text = await complete_with_images(_build_reference_safety_prompt(request), images, on_reasoning)
        try:
            safety_result = parse_reference_safety_json(_extract_json(safety_text))
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("Analysis LLM returned an invalid safety result") from exc
        if not safety_result.allowed:
            reason = "reference_adult_explicit" if safety_result.reason == "adult_explicit" else "reference_unusable"
            raise ReferenceRejectedError(reason, safety_result.message or reason)

        analysis_text = await complete_with_images(_build_detail_analysis_prompt(request), images, on_reasoning)
        try:
            return parse_detail_analysis_json(_extract_json(analysis_text))
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("Analysis LLM returned invalid detail analysis JSON") from exc


def _encode_reference_images(reference_keys: list[str]) -> list[str]:
    settings = get_settings()
    reference_root = resolve_repo_path(settings.reference_upload_dir)
    encoded: list[str] = []
    missing: list[str] = []
    for reference_key in reference_keys:
        path = _resolve_uploaded_reference_path(reference_key, reference_root)
        if path is None or not path.is_file():
            missing.append(str(reference_key))
            continue
        try:
            with Image.open(path) as image:
                encoded.append(to_data_uri(image.convert("RGB"), int(settings.analysis_llm_max_image_side)))
        except (OSError, UnidentifiedImageError) as exc:
            raise RuntimeError(f"Detail analysis reference image is unreadable: {reference_key}") from exc
    if missing:
        raise RuntimeError("Detail analysis reference image not found: " + ", ".join(missing))
    if not encoded:
        raise RuntimeError("Detail analysis requires at least one uploaded user reference")
    return encoded


async def complete_with_images(
    prompt: str,
    images: list[str],
    on_reasoning: ReasoningCallback | None = None,
) -> str:
    settings = get_settings()
    base_url, api_key, model = resolve_analysis_endpoint(settings)
    if not base_url or not model:
        raise RuntimeError("Analysis LLM base URL and model must be set")
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content += [{"type": "image_url", "image_url": {"url": image}} for image in images]
    payload: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": content}]}
    try:
        result = await chat_api.stream_chat_completion(
            base_url,
            api_key,
            payload,
            extra_body=analysis_extra_body(settings),
            timeout=float(settings.codex_detail_analysis_timeout_seconds),
            on_reasoning=on_reasoning,
        )
    except chat_api.ChatAPIError as exc:
        raise RuntimeError(f"Analysis LLM {exc}") from exc
    return chat_api.message_text(result.message)


def analysis_extra_body(settings: Any) -> dict[str, Any]:
    try:
        return chat_api.parse_extra_body(settings.agent_llm_extra_body)
    except ValueError:
        logger.warning("Ignoring invalid AGENT_LLM_EXTRA_BODY for analysis")
        return {}


def _extract_json(text: str) -> str:
    """Models sometimes wrap the JSON object in prose or a code fence; keep just the object."""
    stripped = text.strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    return stripped[start : end + 1] if 0 <= start < end else stripped
