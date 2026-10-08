from typing import Literal, cast


GenerationMode = Literal[
    "front_design",
    "front_revision",
    "front_local_revision",
    "turnaround",
    "character_front",
    "character_revision",
    "character_turnaround",
]

DEFAULT_GENERATION_MODE: GenerationMode = "front_design"
AI_OUTPUT_LANDMARKS_ENABLED = False
VALID_GENERATION_MODES = {
    "front_design",
    "front_revision",
    "front_local_revision",
    "turnaround",
    "character_front",
    "character_revision",
    "character_turnaround",
}
# Stage 1: 2D character design sheets (no finished-product references, no head shell look).
CHARACTER_DESIGN_MODES = {"character_front", "character_revision", "character_turnaround"}
# Modes that produce a 3:2 four-view sheet instead of an 800x1100 portrait.
TURNAROUND_MODES = {"turnaround", "character_turnaround"}


def normalize_generation_mode(value: str | None) -> GenerationMode:
    if value in VALID_GENERATION_MODES:
        return cast(GenerationMode, value)
    return DEFAULT_GENERATION_MODE


def is_turnaround_mode(generation_mode: str | None) -> bool:
    return generation_mode in TURNAROUND_MODES


def is_character_design_mode(generation_mode: str | None) -> bool:
    return generation_mode in CHARACTER_DESIGN_MODES


def expected_output_count(generation_mode: str | None) -> int:
    normalize_generation_mode(generation_mode)
    return 1


def expected_output_indexes(generation_mode: str | None) -> list[int]:
    return list(range(1, expected_output_count(generation_mode) + 1))
