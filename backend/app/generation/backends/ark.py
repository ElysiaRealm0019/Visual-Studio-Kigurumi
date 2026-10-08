from typing import Any

import httpx
from PIL import Image

from app.generation.backends.image_api import ApiRequest, HttpImageBackend, to_data_uri
from app.generation.modes import is_turnaround_mode


class ArkImageBackend(HttpImageBackend):
    """Volcengine Ark (Agent Plan) Seedream via /images/generations.

    Reference images go into one ``image`` list; the platform watermark is turned off with
    ``watermark: false`` and V.S.K adds its own AI label afterwards.
    """

    name = "ark"
    display_name = "Volcengine Ark"

    def reference_limit(self, settings: Any) -> int:
        return max(1, int(settings.ark_max_reference_images))

    def api_key(self, settings: Any) -> str:
        return settings.ark_api_key

    def api_key_env_name(self) -> str:
        return "ARK_API_KEY"

    def workspace_dir(self, settings: Any) -> str:
        return settings.ark_workspace_dir

    def timeout_seconds(self, settings: Any) -> float:
        return float(settings.ark_timeout_seconds)

    def max_retries(self, settings: Any) -> int:
        return int(settings.ark_max_retries)

    def model_name(self, settings: Any) -> str:
        return settings.ark_image_model

    def retryable_status_codes(self) -> set[int]:
        return {429, 500, 502, 503, 504}

    def build_request(
        self, settings: Any, prompt: str, images: list[Image.Image], trace_id: str, generation_mode: str
    ) -> ApiRequest:
        max_side = int(settings.ark_max_input_side)
        payload: dict[str, Any] = {
            "model": settings.ark_image_model,
            "prompt": prompt,
            "size": _size_for_mode(settings, generation_mode),
            "output_format": "png",
            "response_format": "url",
            "watermark": False,
        }
        encoded = [to_data_uri(image, max_side) for image in images]
        if encoded:
            payload["image"] = encoded[0] if len(encoded) == 1 else encoded
        return ApiRequest(
            url=f"{settings.ark_base_url.rstrip('/')}/images/generations",
            headers={
                "Authorization": f"Bearer {settings.ark_api_key.strip()}",
                "X-Client-Request-Id": trace_id,
            },
            payload=payload,
        )

    def trace_id_from_response(self, response: httpx.Response) -> str | None:
        return response.headers.get("x-request-id") or response.headers.get("x-tt-logid")

    def extract_image(self, body: Any) -> tuple[str, str]:
        item = body["data"][0]
        if item.get("url"):
            return "url", item["url"]
        return "b64", item["b64_json"]


def _size_for_mode(settings: Any, generation_mode: str) -> str:
    if is_turnaround_mode(generation_mode):
        return settings.ark_turnaround_size
    if generation_mode == "front_local_revision":
        return settings.ark_local_edit_size
    return settings.ark_front_size
