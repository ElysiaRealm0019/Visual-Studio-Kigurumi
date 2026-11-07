"""Parameter validation and size derivation for the head-shell build (pure Python, no bpy).

Shared by blender/build_shell.py (inside Blender) and backend/app/blender/schema.py (loaded by
path), so the clamping rules and the s computation exist in exactly one place.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "params" / "schema.json"
DEFAULT_VIEW_SPACING_MM = 68.3
# Pulsar neck opening at s = 1 in its own plane (tilted 12.2°): minor = left-right, major =
# front-back. Measured 2026-10-14 on the subdivided outer surface; only used for the neck report.
NECK_REF_MM = {"minor": 130.1, "major": 132.6}


def load_schema(path: str | Path | None = None) -> dict[str, Any]:
    with open(path or SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def schema_version_warning(payload_version: int | None, current_version: int) -> str | None:
    """Warning for a snapshot written against another schema version; None when it matches."""
    if payload_version is None or payload_version == current_version:
        return None
    return f"快照 schema_version={payload_version} 与当前 {current_version} 不一致，按当前 schema 校验"


def defaults(schema: dict[str, Any]) -> dict[str, Any]:
    return {p["key"]: _copy(p.get("default")) for p in schema["params"] if not p.get("readonly")}


def _copy(value: Any) -> Any:
    return list(value) if isinstance(value, list) else value


def validate(schema: dict[str, Any], params: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """Fill defaults, clamp to range and coerce types. Returns (clean params, warnings).

    Never raises on bad values: out-of-range numbers are clamped, bad enum/type values fall back to
    the default, unknown keys are dropped. Every change is reported in the warnings list.
    """
    params = dict(params or {})
    spec = {p["key"]: p for p in schema["params"]}
    clean = defaults(schema)
    warnings: list[str] = []
    for key, value in params.items():
        p = spec.get(key)
        if p is None:
            warnings.append(f"未知参数已忽略: {key}")
            continue
        if p.get("readonly"):
            continue
        ok, coerced, note = _coerce(p, value)
        if note:
            warnings.append(f"{key}: {note}")
        if ok:
            clean[key] = coerced
    return clean, warnings


def _coerce(p: dict[str, Any], value: Any) -> tuple[bool, Any, str]:
    kind = p["type"]
    default = p.get("default")
    if value is None:
        if p.get("nullable"):
            return True, None, ""
        return True, _copy(default), "不可为空，已用默认值"
    if kind in ("float", "int"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            try:
                value = float(value)
            except (TypeError, ValueError):
                return True, _copy(default), f"非数字 {value!r}，已用默认值"
        if isinstance(value, float) and not math.isfinite(value):
            return True, _copy(default), "非有限数，已用默认值"
        lo, hi = p.get("min"), p.get("max")
        v = float(value)
        note = ""
        if lo is not None and v < lo:
            note, v = f"{value} 低于下限 {lo}，已夹取", float(lo)
        elif hi is not None and v > hi:
            note, v = f"{value} 高于上限 {hi}，已夹取", float(hi)
        if kind == "int":
            if v != int(v) and not note:
                note = f"{value} 非整数，已取整"
            v = int(round(v))
        return True, v, note
    if kind == "enum":
        for option in p["options"]:
            if value == option or str(value) == str(option):
                return True, option, ""
        return True, _copy(default), f"非法选项 {value!r}，已用默认值"
    if kind == "multi":
        if not isinstance(value, (list, tuple)):
            return True, _copy(default), "应为列表，已用默认值"
        kept = [o for o in p["options"] if o in value]
        dropped = [v for v in value if v not in p["options"]]
        return True, kept, (f"已忽略非法选项 {dropped}" if dropped else "")
    if kind == "bool":
        if isinstance(value, bool):
            return True, value, ""
        return True, bool(default), f"非布尔值 {value!r}，已用默认值"
    if kind == "string":
        text = str(value)
        limit = p.get("max_length")
        if limit and len(text) > limit:
            return True, text[:limit], f"超过 {limit} 字符，已截断"
        return True, text, ""
    return False, None, f"未知类型 {kind}"


def head_depth_from_circumference(circumference: float, width: float) -> float:
    """Bare head depth from circumference and width (Ramanujan ellipse perimeter, bisection)."""
    a = width / 2.0
    lo, hi = width * 0.5, width * 2.0
    for _ in range(60):
        b = (lo + hi) / 2.0
        perimeter = math.pi * (3 * (a + b) - math.sqrt((3 * a + b) * (a + 3 * b)))
        if perimeter < circumference:
            lo = b
        else:
            hi = b
    return (lo + hi)  # diameter = 2 × semi-axis


def resolve(schema: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Derived values for validated params: required minima, s, recommended tier, actual size."""
    ref = schema["reference_mm"]
    REF = (ref["width"], ref["depth"], ref["height"])
    tiers = schema["tiers"]
    s_lo, s_hi = schema["scale_range"]
    notes: list[str] = []

    wearer_keys = ("fit.head_circumference_mm", "fit.head_width_mm", "fit.head_height_mm")
    has_wearer = all(params.get(k) is not None for k in wearer_keys)
    derived = None
    if has_wearer:
        W = params["fit.head_width_mm"]
        depth_bare = head_depth_from_circumference(params["fit.head_circumference_mm"], W)
        wall = params["structure.wall_thickness_mm"]
        derived = (
            W + params["fit.pad_side_mm"] * 2 + wall * 2,
            depth_bare + params["fit.pad_back_mm"] + wall + params["structure.face_clearance_mm"],
            params["fit.head_height_mm"] + params["fit.pad_top_mm"] + wall + params["base.chin_gap_mm"],
        )
    elif any(params.get(k) is not None for k in wearer_keys):
        notes.append("佩戴者数据不全（头围/头宽/头高需同时填写），未推导需求下限")

    manual = (params.get("base.face_width_mm"), params.get("base.head_depth_mm"), params.get("base.face_height_mm"))
    required = tuple(
        m if m is not None else (derived[i] if derived else REF[i]) for i, m in enumerate(manual)
    )
    ratios = [r / b for r, b in zip(required, REF)]
    s_req = max(ratios)
    tier = params["base.size_tier"]
    if tier == "custom":
        step = params["base.scale_step"]
        s = math.ceil(round(s_req / step, 6)) * step
    else:
        s = tiers[tier]
    s_clamped = min(max(s, s_lo), s_hi)
    if abs(s_clamped - s) > 1e-9:
        notes.append(f"缩放系数 {s:.3f} 超出 {s_lo}–{s_hi}，已夹取")
    s = round(s_clamped, 4)
    meets = s >= s_req - 1e-9
    recommended = next((k for k, v in sorted(tiers.items(), key=lambda kv: kv[1]) if v >= s_req - 1e-9), "custom")
    if not meets:
        notes.append(f"所选档位不满足需求下限（需要 s ≥ {s_req:.3f}），推荐 {recommended}")

    view_spacing = params.get("view.spacing_mm")
    view_source = "manual"
    if view_spacing is None:
        view_spacing = params.get("fit.ipd_mm")
        view_source = "ipd"
    if view_spacing is None:
        view_spacing, view_source = DEFAULT_VIEW_SPACING_MM, "default"

    actual = [round(b * s, 1) for b in REF]
    result: dict[str, Any] = {
        "tier": tier,
        "s_req": round(s_req, 4),
        "s": s,
        "step": params["base.scale_step"] if tier == "custom" else None,
        "recommended_tier": recommended,
        "tightest_axis": ("width", "depth", "height")[ratios.index(s_req)],
        "required_mm": [round(x, 1) for x in required],
        "derived_from_wearer": [round(x, 1) for x in derived] if derived else None,
        "actual_mm": actual,
        "meets": meets,
        "view_spacing_mm": round(float(view_spacing), 2),
        "view_spacing_source": view_source,
        "neck": {"minor_mm": round(NECK_REF_MM["minor"] * s, 1), "major_mm": round(NECK_REF_MM["major"] * s, 1)},
        "notes": notes,
    }
    shoulder = params.get("fit.shoulder_width_mm")
    if shoulder:
        # 1 : x (head incl. wig : shoulders); animegao proportions sit around 1 : 1.5
        result["shoulder_ratio"] = round(shoulder / (actual[0] + params["fit.wig_mm"] * 2), 3)
    return result
