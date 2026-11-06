from pathlib import Path

import pytest

from app.generation import job_store
from app.generation.backends import codex
from app.generation.backends.common import product_reference_kind

PAYLOAD = {
    "character_session_id": "session-a",
    "reference_keys": ["front:references/upload-1/front.webp"],
    "user_requirements": [],
    "user_notes": "",
}

# The eye-symmetry requirement has to reach every stage twice: as prompt text and as a Non-negotiable constraint.
EYE_SYMMETRY_PROMPT_MODES = [
    "character_front",
    "character_revision",
    "character_turnaround",
    "front_design",
    "front_revision",
    "turnaround",
]
EYE_SYMMETRY_CONSTRAINT = (
    "Both eyes must sit at exactly the same height on one horizontal line, mirrored across the face centerline "
    "with the same size and shape; if the reference or draft shows uneven eyes, correct them into one mirrored "
    "pair instead of copying the asymmetry."
)


@pytest.mark.parametrize(
    ("mode", "paths", "expected"),
    [
        ("front_design", [], "none"),
        ("front_design", [Path("ref/product-reference.png")], "matching"),
        ("turnaround", [Path("ref/product-reference.png")], "front_style_only"),
        ("turnaround", [Path("ref/turnaround-reference.png"), Path("ref/product-reference.png")], "matching"),
        ("turnaround", [], "none"),
    ],
)
def test_product_reference_kind(mode, paths, expected):
    assert product_reference_kind(mode, paths) == expected


def test_matching_reference_keeps_original_prompt():
    for mode in ("front_design", "turnaround"):
        payload = {**PAYLOAD, "generation_mode": mode}
        assert codex._build_codex_prompt(payload) == codex._build_codex_prompt(payload, "matching")
    turnaround = codex._build_codex_prompt({**PAYLOAD, "generation_mode": "turnaround"})
    assert "STYLE REFERENCES (added by the application, not by the user)" in turnaround
    assert "Use the attached four-view finished-product reference image only as the layout" in turnaround


@pytest.mark.parametrize("mode", ["front_design", "front_revision", "character_front"])
def test_front_prompts_require_true_symmetric_front_view_and_ears(mode):
    prompt = codex._build_codex_prompt({**PAYLOAD, "generation_mode": mode})
    lower = prompt.lower()

    assert "straight on" in lower
    assert "three-quarter" in lower
    assert "symmetr" in lower
    assert "ears" in lower
    assert "mirror it to complete the other side" in lower
    assert "never remove, hide behind hair, merge, or crop the ears" in lower


@pytest.mark.parametrize("mode", ["turnaround", "character_turnaround"])
def test_turnaround_prompts_keep_ears_consistent_in_every_view(mode):
    prompt = codex._build_codex_prompt({**PAYLOAD, "generation_mode": mode})
    lower = prompt.lower()

    assert "ears" in lower
    assert "every view" in lower


def test_turnaround_with_front_photo_only_drops_layout_claims():
    prompt = codex._build_codex_prompt({**PAYLOAD, "generation_mode": "turnaround"}, "front_style_only")

    assert "四视图参考.png" not in prompt
    assert "four-view finished-product reference image" not in prompt
    assert "single front view of a different character" in prompt
    assert "the last attached image is a photo of a finished kigurumi head shell of a different character" in prompt


@pytest.mark.parametrize("mode", ["front_design", "turnaround"])
def test_missing_product_reference_never_mentions_attached_reference(mode):
    prompt = codex._build_codex_prompt({**PAYLOAD, "generation_mode": mode}, "none")

    assert "商成品参考图.png" not in prompt
    assert "四视图参考.png" not in prompt
    assert "Use the attached finished-product reference image" not in prompt
    assert "No finished-product reference image is attached" in prompt


def test_bridge_prompt_treats_turnaround_reference_as_front_style_only():
    assert codex._bridge_product_reference_kind({"generation_mode": "turnaround"}) == "front_style_only"
    assert codex._bridge_product_reference_kind({"generation_mode": "front_design"}) == "matching"
    prompt = codex._build_codex_candidate_prompt({**PAYLOAD, "generation_mode": "turnaround"}, 1, "front_style_only")
    assert "四视图参考.png" not in prompt


def test_head_shell_front_renders_from_the_design_by_default():
    payload = {**PAYLOAD, "generation_mode": "front_design"}
    prompt = codex._build_codex_prompt(payload, "matching", edit_style_photo=True)
    assert "STYLE PHOTO" not in prompt
    assert "the last attached image is a photo of a finished kigurumi head shell" in prompt
    assert "eggshell-matte paint" in prompt
    assert "never as a real person" in prompt


def test_head_shell_front_edits_the_style_photo_only_on_the_direct_codex_path(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("HEAD_SHELL_EDIT_STYLE_PHOTO", "true")
    get_settings.cache_clear()
    payload = {**PAYLOAD, "generation_mode": "front_design"}
    direct = codex._build_codex_prompt(payload, "matching", edit_style_photo=True)
    assert "The FIRST attached image is the STYLE PHOTO" in direct
    assert "editing the style photo into the user's character" in direct
    # The bridge attaches the photo last and keeps the translate-the-design prompt.
    bridge = codex._build_codex_candidate_prompt(payload, 1, "matching")
    assert "STYLE PHOTO" not in bridge
    assert "the last attached image is a photo of a finished kigurumi head shell" in bridge
    # Without a style photo there is nothing to edit.
    assert "STYLE PHOTO" not in codex._build_codex_prompt(payload, "none", edit_style_photo=True)
    # Revisions start from an existing head shell, never from the style photo.
    revision = codex._build_codex_prompt({**PAYLOAD, "generation_mode": "front_revision"}, "matching", True)
    assert "STYLE PHOTO" not in revision


def test_head_shell_prompts_match_the_control_references():
    for mode in ["front_design", "front_revision", "turnaround"]:
        prompt = codex._build_codex_prompt({**PAYLOAD, "generation_mode": mode})
        # Hair must never spill onto the face (strands used to drape over the eyes and cheeks);
        # flat strands between the eyes are fine, and only the design may cover an eye.
        assert "lie flat against the shell and stay off the face" in prompt
        assert "never in front of the face" in prompt
        assert "spilling onto the shell or the face" in prompt
        assert "Only the character design itself may put hair over an eye" in prompt
        # The mouth is paint on the surface, not a groove carved into the shell.
        assert "never a carved, engraved or incised groove" in prompt
        # Bright even studio light, eggshell-matte skin and airbrushed blush like the control photos.
        assert "neither side of the face may fall into heavy shadow" in prompt
        assert "eggshell-matte paint" in prompt
        assert "airbrushed blush" in prompt
        # Wig fibre reads as grouped clumps with a satin sheen, not glossy separate strands.
        assert "satin sheen, never wet-look or waxy gloss" in prompt
        assert "never from sparse ribbon-like strands" in prompt
        assert "never hatch marks, stroke lines or drawn-on anime blush" in prompt
    bridge_prompt = codex._build_codex_candidate_prompt({**PAYLOAD, "generation_mode": "front_design"}, 1, "matching")
    assert "the face stays fully visible" in bridge_prompt
    assert "never carved into the shell" in bridge_prompt


@pytest.mark.parametrize("mode", EYE_SYMMETRY_PROMPT_MODES)
def test_every_stage_prompt_demands_equal_height_mirrored_eyes(mode):
    prompt = codex._build_codex_prompt({**PAYLOAD, "generation_mode": mode})
    lower = prompt.lower()

    assert "mirrored across the face centerline" in prompt or "mirrored eye design" in prompt
    assert "same height" in lower
    # Uneven eyes in a reference must be corrected, never copied.
    assert "do not copy that asymmetry" in prompt


def test_style_photo_edit_prompt_demands_equal_height_mirrored_eyes(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("HEAD_SHELL_EDIT_STYLE_PHOTO", "true")
    get_settings.cache_clear()
    prompt = codex._build_codex_prompt(
        {**PAYLOAD, "generation_mode": "front_design"}, "matching", edit_style_photo=True
    )
    lower = prompt.lower()

    assert "editing the style photo into the user's character" in prompt
    assert "mirrored across the face centerline" in prompt or "mirrored eye design" in prompt
    assert "same height" in lower
    assert "do not copy that asymmetry" in prompt


@pytest.mark.parametrize(
    ("mode", "default_constraints"),
    [
        ("front_design", job_store._FRONT_DESIGN_CONSTRAINTS),
        ("front_revision", job_store._FRONT_REVISION_CONSTRAINTS),
        ("character_front", job_store._CHARACTER_FRONT_CONSTRAINTS),
        ("character_revision", job_store._CHARACTER_FRONT_CONSTRAINTS),
        ("character_turnaround", job_store._CHARACTER_TURNAROUND_CONSTRAINTS),
        ("turnaround", job_store._TURNAROUND_CONSTRAINTS),
    ],
)
def test_system_constraints_demand_equal_height_mirrored_eyes(mode, default_constraints):
    constraints = job_store._system_constraints_for_mode(mode, [])

    assert EYE_SYMMETRY_CONSTRAINT in constraints
    assert EYE_SYMMETRY_CONSTRAINT in default_constraints
    # The constraint comes after the preserve-the-identity entry, so identity fidelity cannot override it.
    assert constraints.index(EYE_SYMMETRY_CONSTRAINT) > 0
