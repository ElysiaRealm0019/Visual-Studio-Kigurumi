"""Inventory of the hot-reloadable prompt keys: the data behind GET /api/settings/prompts.

Default values are pulled from the owning modules so the endpoint always shows the true built-in fallbacks;
the import happens lazily so importing this registry alone never drags in the generation stack.
"""

from dataclasses import dataclass
from typing import Any

from app.prompts.overrides import get_prompt_list, get_prompt_text, is_overridden


@dataclass(frozen=True)
class PromptField:
    key: str
    kind: str  # "text" | "list"


PROMPT_FIELDS: list[PromptField] = [
    PromptField("style_photo_ignore", "text"),
    PromptField("front_product_style_line", "text"),
    PromptField("turnaround_product_style_line", "text"),
    PromptField("style_line_replacement.front.none", "text"),
    PromptField("style_line_replacement.turnaround.front_style_only", "text"),
    PromptField("style_line_replacement.turnaround.none", "text"),
    PromptField("head_pose", "text"),
    PromptField("watermark_line", "text"),
    PromptField("identity_line", "text"),
    PromptField("hair_fidelity_line", "text"),
    PromptField("no_imposed_hairstyle_line", "text"),
    PromptField("front_ears_line", "text"),
    PromptField("shell_ears_line", "text"),
    PromptField("physical_translation_line", "text"),
    PromptField("anime_face_line", "text"),
    PromptField("head_shell_look", "text"),
    PromptField("head_shell_presentation", "text"),
    PromptField("four_view_layout_line", "text"),
    PromptField("four_view_clean_line", "text"),
    PromptField("codex.tool_requirement", "text"),
    PromptField("codex.tool_output_note", "text"),
    PromptField("analysis.detail_prompt", "text"),
    PromptField("analysis.safety_prompt", "text"),
    PromptField("constraints.front_design", "list"),
    PromptField("constraints.front_revision", "list"),
    PromptField("constraints.character_front", "list"),
    PromptField("constraints.character_turnaround", "list"),
    PromptField("constraints.turnaround", "list"),
    PromptField("constraints.landmark_lines", "list"),
    PromptField("agent.system_prompt", "text"),
]


def _prompting_defaults() -> dict[str, Any]:
    from app.generation.backends import prompting

    return {
        "style_photo_ignore": prompting.STYLE_PHOTO_IGNORE,
        "front_product_style_line": prompting.FRONT_PRODUCT_STYLE_LINE_BASE + " " + prompting.STYLE_PHOTO_IGNORE,
        "turnaround_product_style_line": (
            prompting.TURNAROUND_PRODUCT_STYLE_LINE_BASE + " " + prompting.STYLE_PHOTO_IGNORE
        ),
        "style_line_replacement.front.none": prompting._FRONT_NONE_STYLE_LINE_BASE,
        "style_line_replacement.turnaround.front_style_only": (
            prompting._TURNAROUND_FRONT_STYLE_ONLY_BASE + " " + prompting.STYLE_PHOTO_IGNORE
        ),
        "style_line_replacement.turnaround.none": prompting._TURNAROUND_NONE_STYLE_LINE_BASE,
        "head_pose": prompting.HEAD_POSE,
        "watermark_line": prompting.WATERMARK_LINE,
        "identity_line": prompting._IDENTITY_LINE,
        "hair_fidelity_line": prompting._HAIR_FIDELITY_LINE,
        "no_imposed_hairstyle_line": prompting._NO_IMPOSED_HAIRSTYLE_LINE,
        "front_ears_line": prompting._FRONT_EARS_LINE + ".",
        "shell_ears_line": (
            prompting._FRONT_EARS_LINE + ", even when a finished-product style reference shows a shell without ears."
        ),
        "physical_translation_line": prompting.PHYSICAL_TRANSLATION_LINE,
        "anime_face_line": prompting.ANIME_FACE_LINE,
        "head_shell_look": prompting.HEAD_SHELL_LOOK,
        "head_shell_presentation": prompting.HEAD_SHELL_PRESENTATION,
        "four_view_layout_line": prompting._FOUR_VIEW_LAYOUT_LINE,
        "four_view_clean_line": prompting._FOUR_VIEW_CLEAN_LINE,
    }


def prompt_inventory() -> dict[str, dict[str, Any]]:
    """Every key with its built-in default, the current effective value, and whether the file overrides it."""
    from app.agent.runner import SYSTEM_PROMPT
    from app.generation import job_store
    from app.generation.backends import analysis, codex

    defaults = _prompting_defaults()
    defaults.update(
        {
            "codex.tool_requirement": codex.IMAGE_GENERATION_TOOL_REQUIREMENT,
            "codex.tool_output_note": codex.TOOL_OUTPUT_COLLECTION_NOTE,
            "analysis.detail_prompt": analysis.DETAIL_ANALYSIS_PROMPT,
            "analysis.safety_prompt": analysis.REFERENCE_SAFETY_PROMPT,
            "constraints.front_design": job_store._FRONT_DESIGN_CONSTRAINTS,
            "constraints.front_revision": job_store._FRONT_REVISION_CONSTRAINTS,
            "constraints.character_front": job_store._CHARACTER_FRONT_CONSTRAINTS,
            "constraints.character_turnaround": job_store._CHARACTER_TURNAROUND_CONSTRAINTS,
            "constraints.turnaround": job_store._TURNAROUND_CONSTRAINTS,
            "constraints.landmark_lines": job_store._LANDMARK_CONSTRAINTS,
            "agent.system_prompt": SYSTEM_PROMPT,
        }
    )

    inventory: dict[str, dict[str, Any]] = {}
    for field in PROMPT_FIELDS:
        default = defaults[field.key]
        value = (
            get_prompt_text(field.key, default) if field.kind == "text" else get_prompt_list(field.key, default)
        )
        inventory[field.key] = {
            "kind": field.kind,
            "default": default,
            "value": value,
            "overridden": is_overridden(field.key),
        }
    return inventory
