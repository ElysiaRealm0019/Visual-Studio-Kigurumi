from app.core.config import Settings, get_settings
from app.generation.backends.ark import ArkImageBackend
from app.generation.backends.claude_code import ClaudeCodeLLMBackend
from app.generation.backends.codex import (
    CodexBridgeImageProvider,
    CodexImageProvider,
    _build_codex_candidate_prompt,
    _build_codex_prompt,
    request_codex_bridge_cancel,
)
from app.generation.backends.fixture import FixtureImageProvider, MockProvider
from app.generation.backends.openai_compatible import OpenAICompatibleLLMBackend
from app.generation.backends.prompting import _format_detail_lock_for_prompt
from app.generation.backends.siliconflow import SiliconFlowImageBackend
from app.generation.backends.types import (
    ImageGenerationProvider,
    ProviderOutput,
    ProviderUsage,
    ReferenceRejectedError,
    ReferenceSafetyResult,
)
from app.generation.detail_analysis import DetailAnalysisProviderRequest, DetailAnalysisProviderResult

__all__ = [
    "ArkImageBackend",
    "ClaudeCodeLLMBackend",
    "CodexBridgeImageProvider",
    "CodexImageProvider",
    "CompositeProvider",
    "FixtureImageProvider",
    "ImageGenerationProvider",
    "MockProvider",
    "OpenAICompatibleLLMBackend",
    "ProviderOutput",
    "ProviderUsage",
    "ReferenceRejectedError",
    "ReferenceSafetyResult",
    "SiliconFlowImageBackend",
    "_build_codex_candidate_prompt",
    "_build_codex_prompt",
    "_format_detail_lock_for_prompt",
    "get_generation_provider",
    "request_generation_cancel",
    "resolve_backend_names",
]

LLM_BACKENDS = {"fixture", "codex", "claude_code", "openai_compatible"}
IMAGE_BACKENDS = {"fixture", "codex", "codex_bridge", "siliconflow", "ark"}
_LEGACY_BACKENDS: dict[str, tuple[str, str]] = {
    "fixture": ("fixture", "fixture"),
    "mock": ("fixture", "fixture"),
    "codex": ("codex", "codex"),
    "codex_bridge": ("codex", "codex_bridge"),
}


class CompositeProvider(ImageGenerationProvider):
    """Routes reference analysis to an LLM backend and image generation to an image backend."""

    def __init__(
        self,
        *,
        llm_name: str,
        llm: ImageGenerationProvider,
        image_name: str,
        image: ImageGenerationProvider,
    ) -> None:
        self.llm_name = llm_name
        self.image_name = image_name
        self.llm = llm
        self.image = image
        self.name = f"{llm_name}+{image_name}"

    @property
    def is_fixture(self) -> bool:
        return self.image.is_fixture

    @property
    def uses_codex(self) -> bool:
        return self.llm.uses_codex or self.image.uses_codex

    @property
    def supports_local_revision(self) -> bool:
        return self.image.supports_local_revision

    @property
    def uses_codex_bridge(self) -> bool:
        return self.image.uses_codex_bridge

    async def analyze_reference_details(
        self,
        request: DetailAnalysisProviderRequest,
    ) -> DetailAnalysisProviderResult:
        return await self.llm.analyze_reference_details(request)

    async def generate(self, job_id: str, prompt_payload: dict) -> list[ProviderOutput]:
        return await self.image.generate(job_id, prompt_payload)

    async def generate_incremental(self, job_id: str, prompt_payload: dict):
        async for item in self.image.generate_incremental(job_id, prompt_payload):
            yield item


def resolve_backend_names(settings: Settings) -> tuple[str, str]:
    configured = settings.generation_provider.strip().lower()
    if configured not in _LEGACY_BACKENDS:
        raise ValueError(f"Unsupported generation provider: {settings.generation_provider}")
    legacy_llm, legacy_image = _LEGACY_BACKENDS[configured]
    llm_name = settings.llm_provider.strip().lower() or legacy_llm
    image_name = settings.image_provider.strip().lower() or legacy_image
    if llm_name not in LLM_BACKENDS:
        raise ValueError(f"Unsupported LLM provider: {llm_name}")
    if image_name not in IMAGE_BACKENDS:
        raise ValueError(f"Unsupported image provider: {image_name}")
    return llm_name, image_name


def _build_llm_backend(name: str) -> ImageGenerationProvider:
    if name == "fixture":
        return FixtureImageProvider()
    if name == "claude_code":
        return ClaudeCodeLLMBackend()
    if name == "openai_compatible":
        return OpenAICompatibleLLMBackend()
    return CodexImageProvider()


def _build_image_backend(name: str) -> ImageGenerationProvider:
    if name == "fixture":
        return FixtureImageProvider()
    if name == "codex_bridge":
        return CodexBridgeImageProvider()
    if name == "siliconflow":
        return SiliconFlowImageBackend()
    if name == "ark":
        return ArkImageBackend()
    return CodexImageProvider()


def get_generation_provider() -> ImageGenerationProvider:
    llm_name, image_name = resolve_backend_names(get_settings())
    # Combinations that existed before backends were split keep their original provider and name.
    if (llm_name, image_name) == ("fixture", "fixture"):
        return FixtureImageProvider()
    if (llm_name, image_name) == ("codex", "codex"):
        return CodexImageProvider()
    if (llm_name, image_name) == ("codex", "codex_bridge"):
        return CodexBridgeImageProvider()
    return CompositeProvider(
        llm_name=llm_name,
        llm=_build_llm_backend(llm_name),
        image_name=image_name,
        image=_build_image_backend(image_name),
    )


async def request_generation_cancel(provider_name: str, job_id: str, prompt_payload: dict) -> None:
    image_name = provider_name.split("+")[-1]
    if image_name != "codex_bridge":
        return
    await request_codex_bridge_cancel(job_id, prompt_payload)
