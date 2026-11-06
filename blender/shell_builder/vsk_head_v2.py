"""VSK head-shell parameter interface, format `vsk-head` version 2 (the single JSON standard).

Design rules
  * One canonical document per head. Every shape value is a *delta from the Pulsar neutral head* in
    reference space (s = 1, millimetres / degrees); 0 everywhere = neutral, bit-identical to the base.
  * Each shape value is an independent, measurable degree of freedom. Values that only re-expressed
    another one (eye offset / width / tilt vs. corner moves, height scale vs. lid arcs, the 15 sizing
    inputs that all collapse into one scale) were removed — see docs/vsk-head-v2.md for the analysis.
  * Eye shape is given by landmarks: inner / outer corner moves (dx toward the midline +, dz up +) and
    the mid-lid heights. The converter solves the Blender sockets so these landmarks land exactly.
  * The Blender binding (flat `legacy` keys consumed by KiguBench shell_builder.apply) lives only here.

    v2 = spec.normalize(doc)            # fill defaults, validate ranges -> (v2, warnings)
    flat = spec.to_legacy(v2)           # -> KiguBench flat params (schema_version 1)
    v2 = spec.from_legacy(flat)         # old presets -> v2 (eye landmarks via the measured Jacobian)
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np

FORMAT = "vsk-head"
VERSION = 2
HERE = Path(__file__).resolve().parent
_JAC = json.loads((HERE / "vsk_eye_jacobian.json").read_text(encoding="utf-8"))

# ---------------------------------------------------------------- canonical fields
# key: (default, min, max, unit, description). Ranges are the geometric-validity envelope measured
# with vsk.geom (no folds / creases / slit blockage at the extremes), not arbitrary UI limits.
FIELDS: dict[str, tuple] = {
    # size: exactly one scale s; tiers and wearer data are only ways to choose it
    "size.mode": ("tier", None, None, "enum", "tier | scale | wearer"),
    "size.tier": ("standard", None, None, "enum", "lite 0.92 | standard 1.00 | extra 1.09"),
    "size.scale": (1.0, 0.85, 1.30, "", "uniform scale s (mode=scale)"),
    "size.step": (0.01, 0.01, 0.05, "", "rounding step for mode=wearer"),
    "size.wearer.head_circumference_mm": (None, 450, 750, "mm", ""),
    "size.wearer.head_width_mm": (None, 130, 230, "mm", ""),
    "size.wearer.head_height_mm": (None, 180, 290, "mm", ""),
    "size.wearer.ipd_mm": (None, 54, 74, "mm", "also sets the view-slit spacing"),
    "size.wearer.pad_side_mm": (30, 20, 40, "mm", ""),
    "size.wearer.pad_top_mm": (35, 20, 50, "mm", ""),
    "size.wearer.pad_back_mm": (25, 15, 40, "mm", ""),
    "size.wearer.chin_gap_mm": (10, 5, 20, "mm", ""),
    "size.wearer.face_clearance_mm": (8, 0, 20, "mm", ""),
    # face (Pulsar morph targets + generic sliders)
    "face.slide": (0.0, -1.5, 1.2, "", "Pulsar 滑移脸型 (0 = mature, 1 = youthful); > 1.2 sculpts an under-eye hollow (visual check 11-03)"),
    "face.cheek": (0.0, -0.2, 1.0, "", "Pulsar 腮帮子"),
    "face.lower_length_mm": (0.0, -6.25, 8.44, "mm", "chin down +"),
    "face.lower_recede_mm": (0.0, -10, 8.75, "mm", "chin back +"),
    "face.jaw_width_mm": (0.0, -3.12, 2.19, "mm", "jaw half-width +"),
    # eye landmarks (left eye; mirrored)
    "eye.inner_dx_mm": (0.0, -13.75, 6.25, "mm", "inner corner, + toward the midline"),
    "eye.inner_dz_mm": (0.0, -1.88, 6.88, "mm", "inner corner, + up"),
    "eye.outer_dx_mm": (0.0, -2.5, 2.5, "mm", "outer corner, + toward the midline"),
    "eye.outer_dz_mm": (0.0, -3.75, 2.5, "mm", "outer corner, + up"),
    "eye.opening_mm": (0.0, -5.28, 5.25, "mm", "eye opening height, shape-preserving (uniform vertical scale of the rim)"),
    "eye.upper_lid_mm": (0.0, -9.75, 9.75, "mm", "upper-lid arc height, + rounder"),
    "eye.lower_lid_mm": (0.0, -6.0, 8.62, "mm", "lower-lid arc, + raised (flatter)"),
    "eye.upper_peak_mm": (0.0, -5.62, 6.56, "mm", "upper-lid apex shift, + outward"),
    "eye.lower_peak_mm": (0.0, -2.5, 7.19, "mm", "lower-lid low point shift, + outward"),
    # nose / mouth
    "eye.outer_sharp": (0.0, 0.0, 0.9, "", "outer corner sharpness: 0 = Pulsar round, 1 = pointed (pinch + crease)"),
    "eye.inner_sharp": (0.0, 0.0, 0.55, "", "inner corner sharpness: 0 = Pulsar round, 1 = pointed (pinch + crease)"),
    "nose.height_mm": (0.0, -7.19, 8.75, "mm", "nose tip up +"),
    "nose.protrusion_mm": (0.0, -10, 7.81, "mm", "nose tip forward +"),
    "mouth.width_mm": (0.0, -9.62, 24.7, "mm", "mouth width delta (neutral 24.7 mm)"),
    "mouth.dz_mm": (0.0, -10, 10, "mm", "mouth (with lip relief) up + / down - along the face; nose and chin stay"),
    "mouth.smile_deg": (0.0, -6.25, 8.75, "deg", "corner lift"),
    # expression
    "expression.eye_close": (0.0, -0.2, 1.0, "", ""),
    "expression.mouth_open": (0.0, 0.0, 1.2, "", ""),
    # attached parts
    "parts.lash_style": (0, 0, 15, "", "睫毛类型"),
    "parts.ear_dz_mm": (0.0, -20, 20, "mm", ""),
    "parts.ear_tilt_deg": (0.0, -30, 30, "deg", ""),
    "parts.view_slit_spacing_mm": (None, 54, 80, "mm", "None = wearer ipd or 64"),
    "parts.view_slit_dz_mm": ("auto", -10, 10, "mm", "'auto' tucks the slit behind the lash top"),
    # manufacturing (real mm, never scaled)
    "build.wall_mm": (2.62, 1.5, 5.0, "mm", ""),
    "build.split": (0.78, 0.0, 0.85, "", "front/back split position"),
    "build.lock_type": (1, None, None, "enum", ""),
    "build.lock_offset_mm": (11, 0, 30, "mm", ""),
    "build.back_fillet": (0, 0, 5, "", ""),
    "build.drain_holes": (29, 0, 29, "", ""),
    "build.remesh_mm": (0.1, 0.05, 0.5, "mm", ""),
    "build.text_front": ("E.N.S.O", None, None, "str", ""),
    "build.text_back": ("VER 1.0", None, None, "str", ""),
    "build.text_eyeline": ("xx", None, None, "str", ""),
    "build.engrave_eyeline": (False, None, None, "bool", ""),
    "output.parts": (["front_shell", "back_shell", "eyeline"], None, None, "list", ""),
    "output.views": (["front", "three_quarter", "side"], None, None, "list", ""),
    "output.formats": (["glb", "stl"], None, None, "list", ""),
    "output.render_px": (1280, 256, 2048, "px", ""),
}

TIERS = {"lite": 0.92, "standard": 1.0, "extra": 1.09}

# v2 key -> legacy key, for 1:1 fields
DIRECT = {
    "face.slide": "face.slide", "face.cheek": "face.cheek", "face.lower_length_mm": "face.lower_length_mm",
    "face.lower_recede_mm": "face.lower_recede_mm", "face.jaw_width_mm": "face.jaw_width_mm",
    "nose.height_mm": "nose.tip_height_mm", "nose.protrusion_mm": "nose.tip_protrusion_mm",
    "mouth.smile_deg": "mouth.smile_deg", "mouth.dz_mm": "mouth.offset_z_mm", "eye.outer_sharp": "eye.outer_sharp", "eye.inner_sharp": "eye.inner_sharp", "expression.eye_close": "face.eye_close",
    "expression.mouth_open": "face.mouth_open", "parts.lash_style": "lashes.style",
    "parts.ear_dz_mm": "ears.offset_z_mm", "parts.ear_tilt_deg": "ears.tilt_deg",
    "build.wall_mm": "structure.wall_thickness_mm", "build.split": "structure.split_position",
    "build.lock_type": "structure.lock_type", "build.lock_offset_mm": "structure.lock_offset_mm",
    "build.back_fillet": "structure.back_fillet", "build.drain_holes": "structure.drain_holes",
    "build.remesh_mm": "structure.remesh_mm", "build.text_front": "text.front", "build.text_back": "text.back",
    "build.text_eyeline": "text.eyeline", "build.engrave_eyeline": "text.eyeline_engrave",
    "output.parts": "output.parts", "output.views": "output.views", "output.formats": "output.export_formats",
    "output.render_px": "output.render_px",
    "size.step": "base.scale_step",
    "size.wearer.head_circumference_mm": "fit.head_circumference_mm",
    "size.wearer.head_width_mm": "fit.head_width_mm", "size.wearer.head_height_mm": "fit.head_height_mm",
    "size.wearer.ipd_mm": "fit.ipd_mm", "size.wearer.pad_side_mm": "fit.pad_side_mm",
    "size.wearer.pad_top_mm": "fit.pad_top_mm", "size.wearer.pad_back_mm": "fit.pad_back_mm",
    "size.wearer.chin_gap_mm": "base.chin_gap_mm", "size.wearer.face_clearance_mm": "structure.face_clearance_mm",
    "parts.view_slit_spacing_mm": "view.spacing_mm",
}

MOUTH_W0 = 24.7  # mm per unit of 嘴宽倍率 (measured: Δwidth / Δscale)
EYE_OPEN0 = 32.909  # mm of eye opening (upper + lower rim height) per unit of 眼孔高倍率

# eye landmark rows: Ix Iz Ox Oz up_h lo_h  (x of the LEFT eye; inner/outer x sign flipped below so + = midline)
EYE_SOCKETS_SOLVE = ["eye.offset_x_mm", "eye.offset_z_mm", "eye.width_scale", "eye.tilt_deg",
                     "eye.upper_lid_arc_mm", "eye.lower_lid_arc_mm", "eye.upper_peak_shift_mm",
                     "eye.lower_peak_shift_mm"]
EYE_SOCKETS_ALL = EYE_SOCKETS_SOLVE + ["eye.inner_corner_x_mm", "eye.inner_corner_z_mm",
                                       "eye.outer_corner_x_mm", "eye.outer_corner_z_mm"]
EYE_V2 = ["eye.inner_dx_mm", "eye.inner_dz_mm", "eye.outer_dx_mm", "eye.outer_dz_mm",
          "eye.upper_lid_mm", "eye.lower_lid_mm", "eye.upper_peak_mm", "eye.lower_peak_mm"]


def _eye_matrix(sockets):
    """Landmark response (8 v2 eye values) per unit of each socket."""
    cols = []
    for k in sockets:
        Ix, Iz, Ox, Oz, uh, lh, up, lp = _JAC["J"][k][:8]
        # v2 conventions: dx + toward midline (left eye: +x), lids: upper + higher, lower + raised (= -depth);
        # peaks pass through as their own socket values (measured apex shift is quantised by mesh vertices)
        cols.append([Ix, Iz, Ox, Oz, uh / 0.859, -lh / 0.853,
                     1.0 if k == "eye.upper_peak_shift_mm" else 0.0,
                     1.0 if k == "eye.lower_peak_shift_mm" else 0.0])
    return np.array(cols).T


_A_SOLVE = _eye_matrix(EYE_SOCKETS_SOLVE)
_A_ALL = _eye_matrix(EYE_SOCKETS_ALL)
# corner moves caused by one unit of 眼孔高倍率 (lid values in v2 exclude the uniform scaling by definition)
_H_CORNERS = np.array(_JAC["J"]["eye.height_scale"][:4] + [0.0] * 4)


def _neutral_legacy(k):
    return 1.0 if k in ("eye.width_scale", "eye.height_scale", "mouth.width_scale") else 0.0


# ---------------------------------------------------------------- documents
def _get(doc, dotted):
    cur = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def _set(doc, dotted, value):
    parts = dotted.split(".")
    cur = doc
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def flatten(doc):
    out = {}
    for k in FIELDS:
        v, ok = _get(doc, k)
        if ok:
            out[k] = v
    return out


def nest(flat):
    doc = {}
    for k, v in flat.items():
        _set(doc, k, v)
    return doc


def normalize(doc):
    """Fill defaults and clamp to the validity envelope. Returns (flat v2 dict, warnings)."""
    if doc.get("format") not in (None, FORMAT) or doc.get("version") not in (None, VERSION):
        raise ValueError(f"not a {FORMAT} v{VERSION} document")
    warns = []
    known = set(FIELDS)
    for k in _all_keys(doc):
        if k not in known and not k.startswith(("format", "version", "id", "label", "notes", "provenance")):
            warns.append(f"unknown key {k} ignored")
    out = {}
    for k, (dflt, lo, hi, unit, _) in FIELDS.items():
        v, ok = _get(doc, k)
        v = copy.deepcopy(dflt) if not ok else v
        if isinstance(v, (int, float)) and not isinstance(v, bool) and lo is not None:
            if v < lo or v > hi:
                warns.append(f"{k}={v} outside [{lo}, {hi}] → clamped")
                v = min(max(v, lo), hi)
        out[k] = v
    if out["size.mode"] not in ("tier", "scale", "wearer"):
        raise ValueError("size.mode must be tier | scale | wearer")
    if out["size.mode"] == "tier" and out["size.tier"] not in TIERS:
        raise ValueError(f"size.tier must be one of {list(TIERS)}")
    return out, warns


def _all_keys(doc, prefix=""):
    for k, v in doc.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and key != "provenance":
            yield from _all_keys(v, key + ".")
        else:
            yield key


def to_legacy(v2flat):
    """v2 (flat, normalized) -> KiguBench flat params (schema_version 1)."""
    p = v2flat
    out = {}
    for k2, k1 in DIRECT.items():
        out[k1] = p[k2]
    mode = p["size.mode"]
    out["base.size_tier"] = p["size.tier"] if mode == "tier" else "custom"
    if mode == "scale":
        # custom tier with explicit required width = scale * reference width, step 0.0001 -> exact s
        out["base.scale_step"] = 0.0001
        out["base.face_width_mm"] = round(236.9 * float(p["size.scale"]), 4)
        out["base.head_depth_mm"] = None
        out["base.face_height_mm"] = None
        for k in ("fit.head_circumference_mm", "fit.head_width_mm", "fit.head_height_mm"):
            out[k] = None
    out["mouth.width_scale"] = 1.0 + float(p["mouth.width_mm"]) / MOUTH_W0
    vdz = p["parts.view_slit_dz_mm"]
    out["view.auto_fit"] = vdz == "auto"
    out["view.offset_z_mm"] = 0.0 if vdz == "auto" else float(vdz)
    # eyes: solve the 8 sockets so the 8 landmark values land exactly (linear, measured Jacobian)
    hs = float(p["eye.opening_mm"]) / EYE_OPEN0
    out["eye.height_scale"] = 1.0 + hs
    target = np.array([float(p[k]) for k in EYE_V2]) - _H_CORNERS * hs
    x = np.linalg.solve(_A_SOLVE, target)
    for k, v in zip(EYE_SOCKETS_SOLVE, x):
        out[k] = float(v) + _neutral_legacy(k)
    for k in ("eye.inner_corner_x_mm", "eye.inner_corner_z_mm", "eye.outer_corner_x_mm", "eye.outer_corner_z_mm"):
        out[k] = _neutral_legacy(k)
    return out


def from_legacy(flat):
    """KiguBench flat params (any subset) -> v2 flat dict (landmark-equivalent for the eyes)."""
    get = lambda k: flat.get(k, _neutral_legacy(k) if k.startswith(("eye.", "mouth.")) else None)  # noqa: E731
    out = {k: copy.deepcopy(v[0]) for k, v in FIELDS.items()}
    for k2, k1 in DIRECT.items():
        if k1 in flat and flat[k1] is not None:
            out[k2] = flat[k1]
    tier = flat.get("base.size_tier", "standard")
    if tier in TIERS:
        out["size.mode"], out["size.tier"] = "tier", tier
    elif all(flat.get(k) is not None for k in ("fit.head_circumference_mm", "fit.head_width_mm", "fit.head_height_mm")):
        out["size.mode"] = "wearer"
    else:
        ratios = [flat.get("base.face_width_mm") and flat["base.face_width_mm"] / 236.9,
                  flat.get("base.head_depth_mm") and flat["base.head_depth_mm"] / 243.3,
                  flat.get("base.face_height_mm") and flat["base.face_height_mm"] / 284.7]
        r = max([x for x in ratios if x] or [1.0])
        step = float(flat.get("base.scale_step", 0.01))
        out["size.mode"], out["size.scale"] = "scale", round(math.ceil(round(r / step, 6)) * step, 4)
    out["mouth.width_mm"] = round((float(get("mouth.width_scale")) - 1.0) * MOUTH_W0, 3)
    out["parts.view_slit_dz_mm"] = "auto" if flat.get("view.auto_fit", True) else float(flat.get("view.offset_z_mm", 0))
    x = np.array([float(get(k)) - _neutral_legacy(k) for k in EYE_SOCKETS_ALL])
    hs = float(get("eye.height_scale")) - 1.0
    out["eye.opening_mm"] = round(hs * EYE_OPEN0, 3)
    lm = _A_ALL @ x + _H_CORNERS * hs
    for k, v in zip(EYE_V2, lm):
        out[k] = round(float(v), 3)
    return out


def document(v2flat, **meta):
    doc = {"format": FORMAT, "version": VERSION}
    doc.update(meta)
    body = nest({k: v for k, v in v2flat.items()})
    doc.update(body)
    return doc


def shape_delta(v2flat):
    """Only the non-default fields (compact view for reports)."""
    return {k: v for k, v in v2flat.items() if v != FIELDS[k][0]}




def json_schema():
    """JSON Schema (draft 2020-12) of a vsk-head v2 document, generated from FIELDS."""
    def leaf(dflt, lo, hi, unit, desc):
        if unit == "enum":
            return {"default": dflt, "description": desc}
        if unit == "str":
            return {"type": "string", "default": dflt, "description": desc}
        if unit == "bool":
            return {"type": "boolean", "default": dflt, "description": desc}
        if unit == "list":
            return {"type": "array", "items": {"type": "string"}, "default": dflt, "description": desc}
        t = {"type": ["number", "null"] if dflt is None else "number", "default": dflt,
             "description": (desc + (f" [{unit}]" if unit else "")).strip()}
        if lo is not None:
            t["minimum"], t["maximum"] = lo, hi
        return t
    root = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "vsk-head.v2.schema.json",
            "title": "VSK head shell parameters (vsk-head v2)", "type": "object",
            "required": ["format", "version"],
            "properties": {"format": {"const": FORMAT}, "version": {"const": VERSION}, "id": {"type": "string"},
                           "label": {"type": "string"}, "notes": {"type": "string"},
                           "provenance": {"type": "object"}},
            "additionalProperties": False}
    for k, f in FIELDS.items():
        node = root
        parts = k.split(".")
        for p in parts[:-1]:
            node = node["properties"].setdefault(p, {"type": "object", "properties": {}, "additionalProperties": False})
        node["properties"][parts[-1]] = leaf(*f)
    sm = root["properties"]["size"]["properties"]
    sm["mode"]["enum"] = ["tier", "scale", "wearer"]
    sm["tier"]["enum"] = list(TIERS)
    root["properties"]["parts"]["properties"]["view_slit_dz_mm"] = {
        "oneOf": [{"const": "auto"}, {"type": "number", "minimum": -10, "maximum": 10}], "default": "auto"}
    root["properties"]["build"]["properties"]["lock_type"] = {"type": "integer", "default": 1}
    root["properties"]["parts"]["properties"]["lash_style"] = {"type": "integer", "minimum": 0, "maximum": 15, "default": 0}
    return root


if __name__ == "__main__":
    import sys
    if sys.argv[1] == "schema":
        print(json.dumps(json_schema(), ensure_ascii=False, indent=1))
    else:
        src = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        flat = src.get("params", src)
        print(json.dumps(shape_delta(from_legacy(flat)), ensure_ascii=False, indent=1))
