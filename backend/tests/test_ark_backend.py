import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.core.config import get_settings
from app.generation import provider as provider_module
from app.generation.backends import ark, common, image_api

IMAGE_URL = "https://ark-cdn.example.test/result.png"


def png_bytes(size: tuple[int, int], color: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    # Relative ref/ paths must not pick up the developer's real reference images.
    monkeypatch.setattr(common, "resolve_repo_path", lambda value: tmp_path / value)
    reference_root = tmp_path / "references" / "upload-1"
    reference_root.mkdir(parents=True)
    for name, color in [("front.png", "blue"), ("side.png", "green"), ("back.png", "yellow"), ("ear.png", "pink")]:
        (reference_root / name).write_bytes(png_bytes((600, 800), color))
    product_reference = tmp_path / "product-reference.png"
    product_reference.write_bytes(png_bytes((900, 1200), "white"))
    settings = type(
        "Settings",
        (),
        {
            "ark_api_key": "ark-test-key",
            "ark_base_url": "https://ark.example.test/api/plan/v3/",
            "ark_image_model": "doubao-seedream-5-0-pro",
            "ark_front_size": "2K",
            "ark_turnaround_size": "2K-turnaround",
            "ark_local_edit_size": "1K",
            "ark_max_reference_images": 4,
            "ark_timeout_seconds": 10,
            "ark_max_retries": 1,
            "ark_max_input_side": 2048,
            "ark_workspace_dir": str(tmp_path / "ark"),
            "codex_output_dir": str(tmp_path / "generated"),
            "codex_product_reference_path": str(product_reference),
            "generated_public_prefix": "/api/generated",
            "reference_upload_dir": str(tmp_path / "references"),
        },
    )()
    watermarked: list[Path] = []
    monkeypatch.setattr(image_api, "get_settings", lambda: settings)
    monkeypatch.setattr(image_api, "apply_kigcraft_watermark", lambda path: watermarked.append(path))
    monkeypatch.setattr(image_api, "RETRY_DELAYS_SECONDS", (0.0,))
    return settings, tmp_path, watermarked


def install(monkeypatch, handler) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        image_api, "create_http_client", lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(recording))
    )
    return requests


def url_response(request: httpx.Request) -> httpx.Response:
    if str(request.url) == IMAGE_URL:
        return httpx.Response(200, content=png_bytes((1600, 2200), "red"))
    return httpx.Response(200, json={"model": "doubao-seedream-5-0-pro", "data": [{"url": IMAGE_URL, "size": "1600x2200"}]})


def payload(**extra) -> dict:
    return {
        "character_session_id": "session-a",
        "generation_mode": "front_design",
        "reference_keys": [
            "front:references/upload-1/front.png",
            "side:references/upload-1/side.png",
            "back:references/upload-1/back.png",
            "accessory:references/upload-1/ear.png",
        ],
        **extra,
    }


async def test_ark_front_design_sends_image_list_and_disables_platform_watermark(env, monkeypatch):
    _settings, tmp_path, watermarked = env
    requests = install(monkeypatch, url_response)

    outputs = await ark.ArkImageBackend().generate("job-1", payload())

    generation = requests[0]
    assert str(generation.url) == "https://ark.example.test/api/plan/v3/images/generations"
    assert generation.headers["Authorization"] == "Bearer ark-test-key"
    body = json.loads(generation.content)
    assert body["model"] == "doubao-seedream-5-0-pro"
    assert body["size"] == "2K"
    assert body["watermark"] is False
    assert "sequential_image_generation" not in body
    # front + product style + side + one contact sheet for back and ear (limit 4)
    assert len(body["image"]) == 4
    assert all(item.startswith("data:image/png;base64,") for item in body["image"])
    last = Image.open(io.BytesIO(base64.b64decode(body["image"][3].split(",", 1)[1])))
    assert last.height == image_api.CONTACT_SHEET_HEIGHT and last.width > last.height
    assert "Image 2 is a finished-product style reference" in body["prompt"]
    assert "Image 3 contains supplemental references" in body["prompt"]
    assert "Image 4 contains supplemental references" in body["prompt"]
    assert [(o.width, o.height) for o in outputs] == [(800, 1100)]
    assert outputs[0].object_key == "ark/job-1/outputs/candidate-1.webp"
    assert watermarked == [tmp_path / "generated" / "session-a" / "job-1" / "outputs" / "candidate-1.webp"]


async def test_ark_single_reference_is_sent_as_string(env, monkeypatch):
    settings, _tmp_path, _watermarked = env
    settings.ark_max_reference_images = 1
    requests = install(monkeypatch, url_response)

    await ark.ArkImageBackend().generate("job-2", payload())

    body = json.loads(requests[0].content)
    assert isinstance(body["image"], str)


async def test_ark_turnaround_uses_turnaround_size(env, monkeypatch):
    requests = install(monkeypatch, url_response)

    await ark.ArkImageBackend().generate(
        "job-3", payload(generation_mode="turnaround", reference_keys=["front:references/upload-1/front.png"])
    )

    assert json.loads(requests[0].content)["size"] == "2K-turnaround"


async def test_ark_accepts_b64_json_response(env, monkeypatch):
    encoded = base64.b64encode(png_bytes((800, 1100), "red")).decode()
    requests = install(monkeypatch, lambda request: httpx.Response(200, json={"data": [{"b64_json": encoded}]}))

    outputs = await ark.ArkImageBackend().generate("job-4", payload())

    assert len(requests) == 1
    assert (outputs[0].width, outputs[0].height) == (800, 1100)


async def test_ark_error_message_is_reported(env, monkeypatch):
    install(
        monkeypatch,
        lambda request: httpx.Response(
            400, json={"error": {"code": "InvalidParameter", "message": "The parameter `size` is invalid"}}
        ),
    )

    with pytest.raises(image_api.ImageApiError, match="size` is invalid"):
        await ark.ArkImageBackend().generate("job-5", payload())


async def test_ark_server_error_is_retried(env, monkeypatch):
    responses = [httpx.Response(500, json={"error": {"message": "internal"}})]

    def handler(request):
        if responses and str(request.url) != IMAGE_URL:
            return responses.pop(0)
        return url_response(request)

    requests = install(monkeypatch, handler)

    outputs = await ark.ArkImageBackend().generate("job-6", payload())

    assert len([r for r in requests if str(r.url) != IMAGE_URL]) == 2
    assert len(outputs) == 1


async def test_ark_missing_key_fails_fast(env, monkeypatch):
    settings, _tmp_path, _watermarked = env
    settings.ark_api_key = ""

    with pytest.raises(RuntimeError, match="ARK_API_KEY"):
        await ark.ArkImageBackend().generate("job-7", payload())


def test_factory_builds_ark(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("GENERATION_PROVIDER", "codex")
    monkeypatch.setenv("LLM_PROVIDER", "codex")
    monkeypatch.setenv("IMAGE_PROVIDER", "ark")
    get_settings.cache_clear()
    try:
        provider = provider_module.get_generation_provider()
    finally:
        get_settings.cache_clear()

    assert provider.name == "codex+ark"
    assert isinstance(provider.image, ark.ArkImageBackend)
    assert provider.supports_local_revision is True
    assert provider.uses_codex is True
