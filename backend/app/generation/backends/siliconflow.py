from typing import Any

import httpx
from PIL import Image

from app.generation.backends.image_api import ApiRequest, HttpImageBackend, ImageApiError, to_data_uri

IMAGE_FIELDS = ("image", "image2", "image3")
SiliconFlowError = ImageApiError


class SiliconFlowImageBackend(HttpImageBackend):
    """SiliconFlow /images/generations. Edit models take up to three images as image/image2/image3."""

    name = "siliconflow"
    display_name = "SiliconFlow"
    max_reference_images = len(IMAGE_FIELDS)

    def api_key(self, settings: Any) -> str:
        return settings.siliconflow_api_key

    def api_key_env_name(self) -> str:
        return "SILICONFLOW_API_KEY"

    def workspace_dir(self, settings: Any) -> str:
        return settings.siliconflow_workspace_dir

    def timeout_seconds(self, settings: Any) -> float:
        return float(settings.siliconflow_timeout_seconds)

    def max_retries(self, settings: Any) -> int:
        return int(settings.siliconflow_max_retries)

    def model_name(self, settings: Any) -> str:
        return settings.siliconflow_image_model

    def build_request(
        self, settings: Any, prompt: str, images: list[Image.Image], trace_id: str, generation_mode: str
    ) -> ApiRequest:
        max_side = int(settings.siliconflow_max_input_side)
        payload: dict[str, Any] = {"model": settings.siliconflow_image_model, "prompt": prompt}
        for field, image in zip(IMAGE_FIELDS, images[: self.max_reference_images]):
            payload[field] = to_data_uri(image, max_side)
        if settings.siliconflow_num_inference_steps > 0:
            payload["num_inference_steps"] = int(settings.siliconflow_num_inference_steps)
        if settings.siliconflow_cfg > 0:
            payload["cfg"] = float(settings.siliconflow_cfg)
        negative_prompt = settings.siliconflow_negative_prompt.strip()
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        return ApiRequest(
            url=f"{settings.siliconflow_base_url.rstrip('/')}/images/generations",
            headers={
                "Authorization": f"Bearer {settings.siliconflow_api_key.strip()}",
                "X-Enable-Watermark": "0",
                "X-Trace-Id": trace_id,
            },
            payload=payload,
        )

    def trace_id_from_response(self, response: httpx.Response) -> str | None:
        return response.headers.get("x-siliconcloud-trace-id")

    def extract_image(self, body: Any) -> tuple[str, str]:
        return "url", body["images"][0]["url"]
