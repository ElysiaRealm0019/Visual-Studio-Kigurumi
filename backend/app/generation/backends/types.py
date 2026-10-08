from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from app.generation.detail_analysis import DetailAnalysisProviderRequest, DetailAnalysisProviderResult
from app.generation.usage import TokenUsage

# Live tool progress callback: (progress 0-100, phase text, **extra event fields).
ReferenceProgress = Callable[..., Awaitable[None]]

FRONT_OUTPUT_WIDTH = 800
FRONT_OUTPUT_HEIGHT = 1100
TURNAROUND_OUTPUT_WIDTH = 3000
TURNAROUND_OUTPUT_HEIGHT = 2000


@dataclass(frozen=True)
class ProviderOutput:
    index: int
    object_key: str
    image_url: str
    width: int = FRONT_OUTPUT_WIDTH
    height: int = FRONT_OUTPUT_HEIGHT
    landmarks: dict[str, dict[str, float]] | None = None


@dataclass(frozen=True)
class ProviderUsage:
    token_usage: TokenUsage


ReferenceSafetyReason = Literal["ok", "adult_explicit", "unusable_reference"]


@dataclass(frozen=True)
class ReferenceSafetyResult:
    allowed: bool
    reason: ReferenceSafetyReason
    message: str = ""


class ReferenceRejectedError(RuntimeError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


class ImageGenerationProvider:
    name = "base"

    @property
    def is_fixture(self) -> bool:
        return self.name in {"fixture", "mock"}

    @property
    def uses_codex(self) -> bool:
        return self.name in {"codex", "codex_bridge"}

    @property
    def supports_local_revision(self) -> bool:
        return self.name == "codex"

    @property
    def uses_codex_bridge(self) -> bool:
        return self.name == "codex_bridge"

    async def generate(self, job_id: str, prompt_payload: dict) -> list[ProviderOutput]:
        raise NotImplementedError

    async def analyze_reference_details(
        self,
        request: DetailAnalysisProviderRequest,
        progress: ReferenceProgress | None = None,
    ) -> DetailAnalysisProviderResult:
        raise NotImplementedError

    async def generate_incremental(self, job_id: str, prompt_payload: dict):
        for output in await self.generate(job_id, prompt_payload):
            yield output
