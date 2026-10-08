from app.generation.backends.common import _default_front_landmarks, _output_dimensions_for_mode
from app.generation.backends.types import ImageGenerationProvider, ProviderOutput, ReferenceProgress
from app.generation.detail_analysis import (
    DetailAnalysisProviderCrop,
    DetailAnalysisProviderRequest,
    DetailAnalysisProviderResult,
    DetailFeature,
)
from app.generation.modes import AI_OUTPUT_LANDMARKS_ENABLED, expected_output_indexes, normalize_generation_mode


class FixtureImageProvider(ImageGenerationProvider):
    name = "fixture"

    async def generate(self, job_id: str, prompt_payload: dict) -> list[ProviderOutput]:
        generation_mode = normalize_generation_mode(str(prompt_payload.get("generation_mode") or "front_design"))
        indexes = expected_output_indexes(generation_mode)
        width, height = _output_dimensions_for_mode(generation_mode)
        landmarks = (
            _default_front_landmarks()
            if AI_OUTPUT_LANDMARKS_ENABLED and generation_mode in {"front_design", "front_revision"}
            else None
        )
        return [
            ProviderOutput(
                index=index,
                object_key=f"fixture/kigurumi-candidate-{index}.webp",
                image_url=f"/api/static/fixtures/kigurumi-candidate-{index}.webp",
                width=width,
                height=height,
                landmarks=landmarks,
            )
            for index in indexes
        ]

    async def analyze_reference_details(
        self,
        request: DetailAnalysisProviderRequest,
        progress: ReferenceProgress | None = None,
    ) -> DetailAnalysisProviderResult:
        source_key = request.reference_keys[0] if request.reference_keys else "front:references/fixture/front.webp"
        return DetailAnalysisProviderResult(
            features=[
                DetailFeature(
                    id="feature-hair",
                    kind="hair",
                    label="Hair",
                    description="Long straight hair with visible bangs",
                ),
                DetailFeature(
                    id="feature-expression",
                    kind="expression",
                    label="Expression",
                    description="Soft sad expression with a small mouth",
                ),
                DetailFeature(
                    id="feature-avoid-smile",
                    kind="avoid",
                    label="Avoid",
                    description="Do not change the expression into a smile",
                ),
            ],
            crops=[
                DetailAnalysisProviderCrop(
                    id="crop-face",
                    kind="expression",
                    description="Eyes and small mouth expression",
                    source_reference_key=source_key,
                    bbox={"x": 0.25, "y": 0.2, "width": 0.5, "height": 0.45},
                )
            ],
        )


MockProvider = FixtureImageProvider
