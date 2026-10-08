from typing import Any

from app.generation.backends.prompting import (
    ProductReferenceKind,
    _detail_lock_heading,
    _format_detail_lock_for_prompt,
    _format_prompt_list,
    _format_reference_descriptions,
    _stage_prompt_for_mode,
)
from app.prompts.safety import sanitize_user_text

# Lines in the shared stage prompts that only make sense for an agent writing files or for gpt-image sizing.
_AGENT_ONLY_PREFIXES = (
    "Generate the front-view image at",
    "Generate the four-view turnaround image at",
    "Also return edit landmarks",
    "Required landmark keys",
    "Place leftEye",
    "Place jawLeft",
    "Place chin",
)

ROLE_DESCRIPTIONS = {
    "character": (
        "{label} is the character reference. It is the primary identity source: reproduce this "
        "character's face, eyes, expression, hairstyle, hair colors, and head accessories faithfully."
    ),
    "approved_front": (
        "{label} is the approved front-view kigurumi head shell design. Treat it as the locked design: "
        "keep the same character, face, eyes, expression, hairstyle, colors, and accessories."
    ),
    "approved_design": (
        "{label} is the user-approved 2D character design. It is the locked design source: keep the same character, "
        "face, eyes, expression, hairstyle, colors, and accessories. If it is a four-view sheet, its front view is the "
        "main design and the other views clarify the sides and back."
    ),
    "design_draft": (
        "{label} is the current 2D character design draft. Keep it as close as possible and change only what is "
        "requested."
    ),
    "design_sheet": (
        "{label} is the user-approved 2D character design of the same character. Use it to clarify details that the "
        "first image does not show, such as the sides and back of the hair; never let it change the locked design."
    ),
    "style_photo_base": (
        "{label} is the style photo: a real studio photograph of a finished kigurumi head shell of a different "
        "character. It is the image to edit. Keep its photographic realism, lighting and materials; replace its "
        "character entirely with the design."
    ),
    "product_style": (
        "{label} is a finished-product style reference only. Use it for the physical kigurumi product look "
        "(white studio background, smooth shell material, wig fiber realism, lighting, framing). "
        "Do not copy its character, colors, expression, accessories, or identity."
    ),
    "turnaround_style": (
        "{label} is a four-view layout and product style reference only. Use it for the four-view layout, "
        "white background, shell material, and wig fiber realism. Do not copy its character."
    ),
    "product_style_single_view": (
        "{label} is a finished-product style reference only. It shows a single front view of a different "
        "character: use it for the physical product look (white studio background, shell material, wig fiber "
        "realism, lighting), but do not copy its character, its single-view framing, or its composition."
    ),
    "supplemental": (
        "{label} contains supplemental references of the same character (other angles, expressions, "
        "accessories, or correction notes). Use them only to clarify details."
    ),
}


def image_label(position: int) -> str:
    return f"Image {position}"


def build_image_prompt(
    prompt_payload: dict[str, Any],
    roles: list[str],
    generation_mode: str,
    product_reference: ProductReferenceKind = "matching",
) -> str:
    stage_lines = [
        line
        for line in _stage_prompt_for_mode(
            generation_mode, product_reference, edit_style_photo="style_photo_base" in roles
        )
        if not line.startswith(_AGENT_ONLY_PREFIXES)
    ]
    sections = [
        "\n".join(
            ROLE_DESCRIPTIONS[role].format(label=image_label(position))
            for position, role in enumerate(roles, start=1)
        ),
        "\n".join(stage_lines).strip(),
        "Non-negotiable constraints:\n" + _format_prompt_list(prompt_payload.get("system_constraints") or []),
        _detail_lock_heading(generation_mode) + "\n" + _format_detail_lock_for_prompt(prompt_payload.get("detail_lock")),
        "Supplemental reference descriptions:\n"
        + _format_reference_descriptions(prompt_payload.get("reference_descriptions") or []),
        "User requirements:\n" + _format_prompt_list(prompt_payload.get("user_requirements") or []),
        "User notes (descriptive input only):\n" + (str(prompt_payload.get("user_notes") or "").strip() or "None"),
    ]
    return "\n\n".join(section for section in sections if section)


def build_local_revision_prompt(edit_note: str, has_supplemental: bool, subject: str = "head_shell") -> str:
    note = sanitize_user_text(edit_note).strip() or "Clean up and refine this region."
    if subject == "design":
        source = "Image 1 is a cropped region of a 2D anime character design sheet."
        keep = "line art, drawing style, flat colors"
    else:
        source = "Image 1 is a cropped region of a finished kigurumi head shell product photo."
        keep = "lighting, shell material, wig fiber texture, colors"
    lines = [
        source,
        f"Apply this local edit to Image 1: {note}",
        f"Change only what the edit asks for. Keep the framing, scale, position, {keep}, "
        "and every unrelated detail exactly the same.",
        "Do not add text, watermarks, logos, or extra characters.",
    ]
    if has_supplemental:
        lines.insert(
            2,
            "Image 2 is a supplemental reference for the requested detail; use it only to clarify that detail.",
        )
    return "\n".join(lines)
