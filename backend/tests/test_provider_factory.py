import pytest

from app.core.config import get_settings
from app.generation import provider as provider_module
from app.generation.backends.types import ImageGenerationProvider, ProviderOutput
from app.generation.detail_analysis import DetailAnalysisProviderRequest, DetailAnalysisProviderResult


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def configure(monkeypatch, generation: str, llm: str = "", image: str = "") -> None:
    monkeypatch.setenv("GENERATION_PROVIDER", generation)
    monkeypatch.setenv("LLM_PROVIDER", llm)
    monkeypatch.setenv("IMAGE_PROVIDER", image)
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("generation", "expected_type", "expected_name"),
    [
        ("fixture", provider_module.FixtureImageProvider, "fixture"),
        ("codex", provider_module.CodexImageProvider, "codex"),
        ("codex_bridge", provider_module.CodexBridgeImageProvider, "codex_bridge"),
    ],
)
def test_legacy_generation_provider_keeps_original_provider(monkeypatch, generation, expected_type, expected_name):
    configure(monkeypatch, generation)

    provider = provider_module.get_generation_provider()

    assert type(provider) is expected_type
    assert provider.name == expected_name


def test_explicit_backends_override_legacy_setting(monkeypatch):
    configure(monkeypatch, "codex", llm="fixture", image="codex_bridge")

    provider = provider_module.get_generation_provider()

    assert isinstance(provider, provider_module.CompositeProvider)
    assert provider.name == "fixture+codex_bridge"
    assert isinstance(provider.llm, provider_module.FixtureImageProvider)
    assert isinstance(provider.image, provider_module.CodexBridgeImageProvider)
    assert provider.uses_codex is True
    assert provider.uses_codex_bridge is True
    assert provider.is_fixture is False
    assert provider.supports_local_revision is False


def test_explicit_backend_matching_legacy_pair_returns_legacy_provider(monkeypatch):
    configure(monkeypatch, "fixture", llm="codex", image="codex")

    provider = provider_module.get_generation_provider()

    assert type(provider) is provider_module.CodexImageProvider


@pytest.mark.parametrize(
    ("generation", "llm", "image", "message"),
    [
        ("unknown", "", "", "Unsupported generation provider"),
        ("codex", "gemini", "", "Unsupported LLM provider"),
        ("codex", "", "dalle", "Unsupported image provider"),
    ],
)
def test_unsupported_backends_raise(monkeypatch, generation, llm, image, message):
    configure(monkeypatch, generation, llm=llm, image=image)

    with pytest.raises(ValueError, match=message):
        provider_module.get_generation_provider()


class RecordingLLM(ImageGenerationProvider):
    name = "recording-llm"

    def __init__(self) -> None:
        self.requests: list[DetailAnalysisProviderRequest] = []

    async def analyze_reference_details(self, request, progress=None):
        self.requests.append(request)
        return DetailAnalysisProviderResult(features=[], crops=[])


class RecordingImage(ImageGenerationProvider):
    name = "recording-image"

    async def generate(self, job_id, prompt_payload):
        return [ProviderOutput(index=1, object_key=f"{job_id}.webp", image_url=f"/{job_id}.webp")]


async def test_composite_routes_analysis_and_generation_to_separate_backends():
    llm = RecordingLLM()
    composite = provider_module.CompositeProvider(
        llm_name="llm",
        llm=llm,
        image_name="image",
        image=RecordingImage(),
    )
    request = DetailAnalysisProviderRequest(
        analysis_id="analysis-a",
        character_session_id="session-a",
        reference_keys=["front:references/upload-1/front.webp"],
    )

    await composite.analyze_reference_details(request)
    outputs = [item async for item in composite.generate_incremental("job-1", {})]

    assert llm.requests == [request]
    assert [output.object_key for output in outputs] == ["job-1.webp"]
    assert composite.name == "llm+image"


async def test_cancel_only_reaches_bridge_image_backend(monkeypatch):
    calls: list[str] = []

    async def fake_cancel(job_id, prompt_payload):
        calls.append(job_id)

    monkeypatch.setattr(provider_module, "request_codex_bridge_cancel", fake_cancel)

    await provider_module.request_generation_cancel("codex", "job-a", {})
    await provider_module.request_generation_cancel("codex_bridge", "job-b", {})
    await provider_module.request_generation_cancel("claude_code+codex_bridge", "job-c", {})
    await provider_module.request_generation_cancel("claude_code+siliconflow", "job-d", {})

    assert calls == ["job-b", "job-c"]
