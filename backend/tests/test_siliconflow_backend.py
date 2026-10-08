import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.core.config import get_settings
from app.generation import provider as provider_module
from app.generation.backends import common, image_api, siliconflow

IMAGE_URL = "https://cdn.example.test/result.png"


def png_bytes(size: tuple[int, int], color: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def decode_data_uri(value: str) -> Image.Image:
    assert value.startswith("data:image/png;base64,")
    return Image.open(io.BytesIO(base64.b64decode(value.split(",", 1)[1])))


class FakeSiliconFlow:
    def __init__(self, responses: list[httpx.Response], result: bytes) -> None:
        self.responses = list(responses)
        self.result = result
        self.generation_requests: list[httpx.Request] = []
        self.downloads = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url == IMAGE_URL:
            self.downloads += 1
            return httpx.Response(200, content=self.result)
        self.generation_requests.append(request)
        return self.responses.pop(0)

    def payload(self, index: int = 0) -> dict:
        return json.loads(self.generation_requests[index].content)


def ok_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"images": [{"url": IMAGE_URL}], "timings": {"inference": 1.2}, "seed": 1},
        headers={"x-siliconcloud-trace-id": "server-trace"},
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    # Relative ref/ paths must not pick up the developer's real reference images.
    monkeypatch.setattr(common, "resolve_repo_path", lambda value: tmp_path / value)
    reference_root = tmp_path / "references"
    for name, size, color in [
        ("front.png", (600, 600), "blue"),
        ("side.png", (400, 800), "green"),
        ("expression.png", (500, 500), "yellow"),
    ]:
        path = reference_root / "upload-1" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes(size, color))
    product_reference = tmp_path / "product-reference.png"
    product_reference.write_bytes(png_bytes((1000, 1000), "white"))
    settings = type(
        "Settings",
        (),
        {
            "siliconflow_api_key": "test-key",
            "siliconflow_base_url": "https://api.example.test/v1/",
            "siliconflow_image_model": "Qwen/Qwen-Image-Edit-2509",
            "siliconflow_num_inference_steps": 30,
            "siliconflow_cfg": 4.0,
            "siliconflow_negative_prompt": "text, watermark",
            "siliconflow_timeout_seconds": 10,
            "siliconflow_max_retries": 2,
            "siliconflow_max_input_side": 2048,
            "siliconflow_workspace_dir": str(tmp_path / "workspace"),
            "codex_output_dir": str(tmp_path / "generated"),
            "codex_product_reference_path": str(product_reference),
            "generated_public_prefix": "/api/generated",
            "reference_upload_dir": str(reference_root),
        },
    )()
    watermarked: list[Path] = []
    monkeypatch.setattr(image_api, "get_settings", lambda: settings)
    monkeypatch.setattr(image_api, "apply_kigcraft_watermark", lambda path: watermarked.append(path))
    monkeypatch.setattr(image_api, "RETRY_DELAYS_SECONDS", (0.0,))
    return settings, tmp_path, watermarked


def install_fake(monkeypatch, fake: FakeSiliconFlow) -> None:
    monkeypatch.setattr(
        image_api,
        "create_http_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
    )


def design_payload(**extra) -> dict:
    return {
        "character_session_id": "session-a",
        "generation_mode": "front_design",
        "reference_keys": [
            "front:references/upload-1/front.png",
            "side:references/upload-1/side.png",
            "expression:references/upload-1/expression.png",
        ],
        "user_requirements": ["soft youthful expression"],
        "user_notes": "keep green hair tips",
        **extra,
    }


async def test_front_design_sends_three_images_and_saves_watermarked_output(env, monkeypatch):
    settings, tmp_path, watermarked = env
    fake = FakeSiliconFlow([ok_response()], png_bytes((1100, 1512), "red"))
    install_fake(monkeypatch, fake)

    outputs = await siliconflow.SiliconFlowImageBackend().generate("job-1", design_payload())

    assert len(fake.generation_requests) == 1
    request = fake.generation_requests[0]
    assert str(request.url) == "https://api.example.test/v1/images/generations"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["X-Enable-Watermark"] == "0"
    assert request.headers["X-Trace-Id"] == "kigcraft-job-1-1"
    payload = fake.payload()
    assert payload["model"] == "Qwen/Qwen-Image-Edit-2509"
    assert payload["num_inference_steps"] == 30
    assert payload["cfg"] == 4.0
    assert payload["negative_prompt"] == "text, watermark"
    assert "image_size" not in payload
    first = decode_data_uri(payload["image"])
    assert abs(first.width / first.height - 800 / 1100) < 0.01
    assert decode_data_uri(payload["image2"]).size == (1000, 1000)
    sheet = decode_data_uri(payload["image3"])
    assert sheet.height == image_api.CONTACT_SHEET_HEIGHT
    assert "Image 1 is the character reference" in payload["prompt"]
    assert "Image 2 is a finished-product style reference" in payload["prompt"]
    assert "Image 3 contains supplemental references" in payload["prompt"]
    assert "keep green hair tips" in payload["prompt"]
    assert "manifest" not in payload["prompt"].lower()
    assert "800x1100" not in payload["prompt"]
    assert fake.downloads == 1

    assert [(output.index, output.width, output.height) for output in outputs] == [(1, 800, 1100)]
    assert outputs[0].image_url == "/api/generated/session-a/job-1/outputs/candidate-1.webp"
    assert outputs[0].object_key == "siliconflow/job-1/outputs/candidate-1.webp"
    public_file = tmp_path / "generated" / "session-a" / "job-1" / "outputs" / "candidate-1.webp"
    with Image.open(public_file) as saved:
        assert saved.format == "WEBP"
        assert saved.size == (800, 1100)
    assert watermarked == [public_file]


async def test_revision_skips_annotation_and_uses_single_supplemental(env, monkeypatch):
    fake = FakeSiliconFlow([ok_response()], png_bytes((800, 1100), "red"))
    install_fake(monkeypatch, fake)
    payload = design_payload(
        generation_mode="front_revision",
        reference_keys=[
            "front:references/upload-1/front.png",
            "annotation:references/upload-1/side.png",
            "supplemental:references/upload-1/expression.png",
        ],
    )

    await siliconflow.SiliconFlowImageBackend().generate("job-2", payload)

    sent = fake.payload()
    assert decode_data_uri(sent["image3"]).size == (500, 500)
    assert "Image 1 is the approved front-view" in sent["prompt"]


async def test_retryable_status_is_retried(env, monkeypatch):
    fake = FakeSiliconFlow(
        [httpx.Response(429, json={"message": "TPM limit reached"}), ok_response()],
        png_bytes((800, 1100), "red"),
    )
    install_fake(monkeypatch, fake)

    outputs = await siliconflow.SiliconFlowImageBackend().generate("job-3", design_payload())

    assert len(fake.generation_requests) == 2
    assert len(outputs) == 1


async def test_retryable_status_gives_up_after_max_retries(env, monkeypatch):
    fake = FakeSiliconFlow(
        [httpx.Response(503, json={"code": 50505, "message": "Model service overloaded"})] * 3,
        b"",
    )
    install_fake(monkeypatch, fake)

    with pytest.raises(siliconflow.SiliconFlowError, match="HTTP 503.*Model service overloaded"):
        await siliconflow.SiliconFlowImageBackend().generate("job-4", design_payload())
    assert len(fake.generation_requests) == 3


async def test_read_timeout_fails_without_retry(env, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr(
        image_api,
        "create_http_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(siliconflow.SiliconFlowError, match="did not respond within 10s"):
        await siliconflow.SiliconFlowImageBackend().generate("job-10", design_payload())
    assert len(calls) == 1


async def test_connect_error_is_retried(env, monkeypatch):
    fake = FakeSiliconFlow([ok_response()], png_bytes((800, 1100), "red"))
    attempts = []

    def handler(request):
        if request.url != IMAGE_URL and not attempts:
            attempts.append(request)
            raise httpx.ConnectError("refused", request=request)
        return fake.handler(request)

    monkeypatch.setattr(
        image_api,
        "create_http_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    outputs = await siliconflow.SiliconFlowImageBackend().generate("job-11", design_payload())

    assert len(attempts) == 1
    assert len(fake.generation_requests) == 1
    assert len(outputs) == 1


async def test_client_error_is_not_retried(env, monkeypatch):
    fake = FakeSiliconFlow([httpx.Response(400, json={"code": 20012, "message": "bad prompt"})], b"")
    install_fake(monkeypatch, fake)

    with pytest.raises(siliconflow.SiliconFlowError, match="bad prompt"):
        await siliconflow.SiliconFlowImageBackend().generate("job-5", design_payload())
    assert len(fake.generation_requests) == 1


async def test_missing_image_url_fails(env, monkeypatch):
    fake = FakeSiliconFlow([httpx.Response(200, json={"images": []})], b"")
    install_fake(monkeypatch, fake)

    with pytest.raises(siliconflow.SiliconFlowError, match="did not include an image"):
        await siliconflow.SiliconFlowImageBackend().generate("job-6", design_payload())


@pytest.mark.parametrize(
    ("returned_size", "expected_size"),
    [((1584, 1056), (1584, 1056)), ((2000, 2000), (3000, 2000)), ((4000, 2000), (3000, 2000)), ((2816, 1584), (2816, 1877))],
)
async def test_turnaround_keeps_native_size_without_upscaling(env, monkeypatch, returned_size, expected_size):
    fake = FakeSiliconFlow([ok_response()], png_bytes(returned_size, "red"))
    install_fake(monkeypatch, fake)
    payload = design_payload(
        generation_mode="turnaround",
        reference_keys=["front:references/upload-1/front.png"],
    )

    outputs = await siliconflow.SiliconFlowImageBackend().generate("job-7", payload)

    assert (outputs[0].width, outputs[0].height) == expected_size
    sent = fake.payload()
    first = decode_data_uri(sent["image"])
    assert abs(first.width / first.height - 1.5) < 0.01
    assert "image3" not in sent
    # Only the front-view product photo exists, so it must not be described as a four-view layout reference.
    assert "Image 2 is a finished-product style reference only. It shows a single front view" in sent["prompt"]
    assert "four-view layout and product style reference" not in sent["prompt"]
    assert "Build the four-view layout only from the written requirements" in sent["prompt"]


async def test_turnaround_reference_is_described_as_layout_reference(env, monkeypatch):
    _settings, tmp_path, _watermarked = env
    turnaround_reference = tmp_path / "turnaround-reference.png"
    turnaround_reference.write_bytes(png_bytes((1500, 1000), "white"))
    monkeypatch.setattr(image_api, "_product_reference_paths_for_mode", lambda mode, settings: [turnaround_reference])
    fake = FakeSiliconFlow([ok_response()], png_bytes((1584, 1056), "red"))
    install_fake(monkeypatch, fake)
    payload = design_payload(generation_mode="turnaround", reference_keys=["front:references/upload-1/front.png"])

    await siliconflow.SiliconFlowImageBackend().generate("job-12", payload)

    sent = fake.payload()
    assert decode_data_uri(sent["image2"]).size == (1500, 1000)
    assert "Image 2 is a four-view layout and product style reference" in sent["prompt"]
    assert "single front view" not in sent["prompt"]


async def test_front_without_product_reference_says_none_attached(env, monkeypatch):
    monkeypatch.setattr(image_api, "_product_reference_paths_for_mode", lambda mode, settings: [])
    fake = FakeSiliconFlow([ok_response()], png_bytes((800, 1100), "red"))
    install_fake(monkeypatch, fake)
    payload = design_payload(reference_keys=["front:references/upload-1/front.png"])

    await siliconflow.SiliconFlowImageBackend().generate("job-13", payload)

    sent = fake.payload()
    assert "image2" not in sent
    assert "No finished-product reference image is attached" in sent["prompt"]
    assert "Use the attached finished-product reference image" not in sent["prompt"]


async def test_local_revision_edits_crop_and_only_changes_masked_area(env, monkeypatch):
    _settings, tmp_path, watermarked = env
    local_root = tmp_path / "local"
    local_root.mkdir()
    Image.new("RGB", (800, 1100), "red").save(local_root / "base.png")
    mask = Image.new("L", (800, 1100), 0)
    mask.paste(255, (300, 400, 400, 500))
    mask.save(local_root / "mask.png")
    fake = FakeSiliconFlow([ok_response()], png_bytes((512, 512), "lime"))
    install_fake(monkeypatch, fake)
    payload = {
        "character_session_id": "session-a",
        "generation_mode": "front_local_revision",
        "reference_keys": [],
        "local_edit": {
            "base_image_path": str(local_root / "base.png"),
            "mask_image_path": str(local_root / "mask.png"),
            "edit_note": "make the bow green",
            "base_width": 800,
            "base_height": 1100,
            "feather_radius_px": 0,
        },
    }

    outputs = await siliconflow.SiliconFlowImageBackend().generate("job-8", payload)

    sent = fake.payload()
    crop = decode_data_uri(sent["image"])
    assert crop.size == (384, 384)
    assert "image2" not in sent
    assert "make the bow green" in sent["prompt"]
    assert (outputs[0].width, outputs[0].height) == (800, 1100)
    with Image.open(tmp_path / "workspace" / "session-a" / "job-8" / "outputs" / "candidate-1.webp") as result:
        result = result.convert("RGB")
        inside = result.getpixel((350, 450))
        outside = result.getpixel((100, 100))
    assert inside[1] > 200 and inside[0] < 60
    assert outside[0] > 200 and outside[1] < 60
    assert len(watermarked) == 1


async def test_missing_api_key_fails_fast(env, monkeypatch):
    settings, _tmp_path, _watermarked = env
    settings.siliconflow_api_key = " "

    with pytest.raises(RuntimeError, match="SILICONFLOW_API_KEY"):
        await siliconflow.SiliconFlowImageBackend().generate("job-9", design_payload())


def test_local_edit_crop_box_expands_and_stays_inside_image():
    mask = Image.new("L", (800, 1100), 0)
    mask.paste(255, (0, 1000, 50, 1100))

    box = image_api.local_edit_crop_box(mask, (800, 1100))

    assert box == (0, 716, 384, 1100)


@pytest.fixture
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_factory_builds_claude_code_with_siliconflow(monkeypatch, clear_settings_cache):
    monkeypatch.setenv("GENERATION_PROVIDER", "codex")
    monkeypatch.setenv("LLM_PROVIDER", "claude_code")
    monkeypatch.setenv("IMAGE_PROVIDER", "siliconflow")
    get_settings.cache_clear()

    provider = provider_module.get_generation_provider()

    assert provider.name == "claude_code+siliconflow"
    assert provider.uses_codex is False
    assert provider.supports_local_revision is True
    assert provider.is_fixture is False


async def test_character_design_has_no_product_reference_and_draws_2d(env, monkeypatch):
    fake = FakeSiliconFlow([ok_response()], png_bytes((800, 1100), "red"))
    install_fake(monkeypatch, fake)

    await siliconflow.SiliconFlowImageBackend().generate("job-d", design_payload(generation_mode="character_front"))

    sent = fake.payload()
    assert "Image 1 is the character reference" in sent["prompt"]
    assert "finished-product style reference" not in sent["prompt"]
    assert "2D character design" in sent["prompt"]
    # The freed product-photo slot goes to the supplemental references.
    assert decode_data_uri(sent["image2"]).size == (400, 800)
    assert decode_data_uri(sent["image3"]).size == (500, 500)


async def test_head_shell_from_approved_design_uses_it_as_primary(env, monkeypatch):
    fake = FakeSiliconFlow([ok_response()], png_bytes((800, 1100), "red"))
    install_fake(monkeypatch, fake)
    payload = design_payload(
        reference_keys=["design:references/upload-1/side.png"],
    )

    await siliconflow.SiliconFlowImageBackend().generate("job-h", payload)

    sent = fake.payload()
    assert "Image 1 is the user-approved 2D character design" in sent["prompt"]
    assert "Image 2 is a finished-product style reference" in sent["prompt"]


async def test_head_shell_turnaround_attaches_design_sheet(env, monkeypatch):
    fake = FakeSiliconFlow([ok_response()], png_bytes((1536, 1024), "red"))
    install_fake(monkeypatch, fake)
    payload = design_payload(
        generation_mode="turnaround",
        reference_keys=["front:references/upload-1/front.png", "design_extra:references/upload-1/side.png"],
    )

    await siliconflow.SiliconFlowImageBackend().generate("job-t", payload)

    assert "Image 3 is the user-approved 2D character design of the same character" in fake.payload()["prompt"]
