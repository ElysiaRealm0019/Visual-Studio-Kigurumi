from pathlib import Path

import pytest

from app.generation.backends import codex
from app.generation.backends.common import product_reference_kind

PAYLOAD = {
    "character_session_id": "session-a",
    "reference_keys": ["front:references/upload-1/front.webp"],
    "user_requirements": [],
    "user_notes": "",
}


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
    assert "fully matte paint finish" in prompt
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
