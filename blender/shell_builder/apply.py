"""Write validated parameters into ProjectPulsar_Base.blend (runs inside Blender 5.1).

Mirrors the in-file text block `VSK_尺寸计算.py`: the head shell's `缩放系数 s` is the single
scale knob (the other objects follow through drivers), but every dependent value is also written
explicitly so a headless build never depends on driver evaluation order.
"""
from __future__ import annotations

import math
from typing import Any

import bpy
import numpy as np
from mathutils import Matrix, Vector

HEAD = "头壳"
HEAD_LOW = "头壳低面封闭"
EAR_Z0_MM = -28.6                      # Pulsar ear attach height (head coords, s = 1)
EAR_PIVOT_MM = (-80.9, 3.9, -28.6)     # ear tilt pivot (world mm, s = 1)
SCALE_GROUPS = ("VSK_等比缩放", "VSK_反缩放", "VSK_位移跟随")
VIEW_FIT_MARGIN_MM = 0.3     # slit bottom sits this far above the lash-piece top edge
VIEW_FIT_WARN_MM = 5.0       # larger auto offsets move the slit away from the wearer's eye line
VIEW_FIT_BIN_MM = 0.25


class ApplyError(RuntimeError):
    pass


def _obj(name: str) -> bpy.types.Object:
    ob = bpy.data.objects.get(name)
    if ob is None:
        raise ApplyError(f"blend 中缺少对象「{name}」")
    return ob


def _inputs(mod: bpy.types.Modifier) -> dict[str, str]:
    return {
        it.name: it.identifier
        for it in mod.node_group.interface.items_tree
        if getattr(it, "in_out", "") == "INPUT"
    }


def _set_input(ob_name: str, mod_name: str, socket: str, value: Any) -> None:
    ob = _obj(ob_name)
    mod = ob.modifiers.get(mod_name)
    if mod is None:
        raise ApplyError(f"「{ob_name}」缺少修改器「{mod_name}」")
    ids = _inputs(mod)
    if socket not in ids:
        raise ApplyError(f"「{ob_name}/{mod_name}」没有输入「{socket}」")
    mod[ids[socket]] = value
    ob.update_tag()


def _ear_transform(offset_z_mm: float, tilt_deg: float) -> tuple[Vector, Any]:
    """Ear pose → local translation / euler for the `VSK耳朵` modifier (world semantics:
    raise along Z, tilt outward about world Y through the attach pivot)."""
    ob = _obj("耳朵")
    M = ob.matrix_world.copy()
    R_M = M.to_3x3()
    O_w = M.translation
    pivot = Vector(EAR_PIVOT_MM) / 1000.0
    dz = Vector((0.0, 0.0, offset_z_mm / 1000.0))
    Rw = Matrix.Rotation(math.radians(-tilt_deg), 3, "Y")   # positive = outward (left-ear source mesh)
    R_loc = R_M.inverted() @ Rw @ R_M
    T_loc = R_M.inverted() @ (Rw @ (O_w - pivot) + pivot + dz - O_w)
    return T_loc, R_loc.to_euler()


def apply_params(p: dict[str, Any], derived: dict[str, Any], *, final_booleans: bool = True) -> dict[str, Any]:
    s = float(derived["s"])

    # 1. uniform scale on every VSK scale/follow modifier (drivers would propagate it too)
    for ob in bpy.data.objects:
        for mod in ob.modifiers:
            if mod.type == "NODES" and mod.node_group and mod.node_group.name in SCALE_GROUPS:
                ids = _inputs(mod)
                if "缩放系数 s" in ids:
                    mod[ids["缩放系数 s"]] = s
                    ob.update_tag()
    for name in (HEAD, HEAD_LOW):
        weld = _obj(name).modifiers.get("焊接")
        if weld is not None:
            weld.merge_threshold = 0.001 * s
    _set_input("视孔", "VSK缩放", "视孔目标间距 mm", float(derived["view_spacing_mm"]))

    # 2. Pulsar face sliders
    for key, socket in (("face.slide", "滑移脸型"), ("face.eye_close", "睁眼大小"),
                        ("face.cheek", "腮帮子"), ("face.mouth_open", "张嘴大小"),
                        ("face.lower_length_mm", "下半脸拉长"), ("face.lower_recede_mm", "下半脸后收"),
                        ("face.jaw_width_mm", "下颌宽度")):
        _set_input(HEAD, "几何节点", socket, float(p[key]))

    # 3. eye / mouth reshaping (head + closed low-poly copy, which also feeds the lash cage)
    features = {
        "眼孔X位移": p["eye.offset_x_mm"], "眼孔Z位移": p["eye.offset_z_mm"],
        "眼孔宽倍率": p["eye.width_scale"], "眼孔高倍率": p["eye.height_scale"],
        "眼孔倾斜角": p["eye.tilt_deg"],
        "外眼角Z位移": p["eye.outer_corner_z_mm"], "内眼角Z位移": p["eye.inner_corner_z_mm"],
        "外眼角X外延": p["eye.outer_corner_x_mm"],
        "上眼睑弧度": p["eye.upper_lid_arc_mm"], "下眼睑弧度": p["eye.lower_lid_arc_mm"],
        "上眼睑峰位": p["eye.upper_peak_shift_mm"], "下眼睑峰位": p["eye.lower_peak_shift_mm"], "内眼角X位移": p["eye.inner_corner_x_mm"],
        "鼻尖高度": p["nose.tip_height_mm"], "鼻尖突出": p["nose.tip_protrusion_mm"],
        "嘴宽倍率": p["mouth.width_scale"], "嘴角上扬角": p["mouth.smile_deg"],
    }
    for key, sock in (("eye.outer_sharp", "外眼角锐度"), ("eye.inner_sharp", "内眼角锐度")):  # 2026-11-05 patch_eye_sharp
        if key in p:
            features[sock] = p[key]
    if "mouth.offset_z_mm" in p:  # added 2026-11-03 (tools/patch_mouth_z.py); older bases lack the socket
        features["嘴Z位移"] = p["mouth.offset_z_mm"]
    for name in (HEAD, HEAD_LOW):
        for socket, value in features.items():
            _set_input(name, "VSK五官", socket, float(value))

    # 4. ears
    T_loc, R_eul = _ear_transform(float(p["ears.offset_z_mm"]), float(p["ears.tilt_deg"]))
    _set_input("耳朵", "VSK耳朵", "平移(m)", [T_loc.x, T_loc.y, T_loc.z])
    _set_input("耳朵", "VSK耳朵", "旋转欧拉(rad)", [R_eul.x, R_eul.y, R_eul.z])

    # 5. process values in real millimetres (not scaled by s)
    head = _obj(HEAD)
    head.modifiers["实体化"].thickness = p["structure.wall_thickness_mm"] / 1000.0
    head.update_tag()
    view = _obj("视孔")
    for mod in view.modifiers:
        if mod.type == "REMESH":
            mod.voxel_size = p["structure.remesh_mm"] / 1000.0
    view.update_tag()
    _set_input("后壳打洞", "VSK孔数", "孔数", int(p["structure.drain_holes"]))

    # 6. shell parts: split, locks, text, booleans
    _set_input("前壳配置", "几何节点", "前后基准值", float(p["structure.split_position"]))
    _set_input("前壳配置", "几何节点", "锁类型", int(p["structure.lock_type"]))
    _set_input("前壳配置", "几何节点", "锁深度位移", float(p["structure.lock_offset_mm"]) / 10.0)
    _set_input("前壳配置", "几何节点", "前壳文字", str(p["text.front"]))
    _set_input("后壳配置", "几何节点", "后壳文字", str(p["text.back"]))
    _set_input("后壳配置", "几何节点", "圆角位置", float(p["structure.back_fillet"]))
    _set_input("眼线配置", "几何节点", "眼线文字", str(p["text.eyeline"]))
    _set_input("眼线配置", "几何节点", "是否刻字", bool(p["text.eyeline_engrave"]))
    for part in ("前壳配置", "后壳配置", "眼线配置"):
        _set_input(part, "几何节点", "是否布尔", bool(final_booleans))

    # 7. lashes
    _set_input("睫毛", "几何节点", "睫毛类型", int(p["lashes.style"]))

    refresh()

    # 8. view slit height (needs the evaluated lash piece, so it goes last)
    return {"view_slit": fit_view_slit(bool(p["view.auto_fit"]), float(p["view.offset_z_mm"]))}


def _world_mm(name: str) -> np.ndarray:
    ob = _obj(name)
    ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        co = np.empty(len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", co)
        M = np.array(ev.matrix_world)
    finally:
        ev.to_mesh_clear()
    return (co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]) * 1000.0


def fit_view_slit(auto: bool, manual_mm: float) -> dict[str, Any]:
    """Move the view slit vertically (world mm, independent of s).

    auto: in the front projection (left half; the part is mirrored) find, per 0.25 mm column, how far
    the lash-piece top edge reaches above the slit bottom, and shift the slit so it clears the
    highest point by VIEW_FIT_MARGIN_MM: the slit is fully open and tucked right behind the lash top.
    """
    ob = _obj("视孔")
    if "vsk_base_z" not in ob:
        ob["vsk_base_z"] = ob.location.z
    base = float(ob["vsk_base_z"])
    ob.location.z = base
    refresh(1)
    info: dict[str, Any] = {"mode": "auto" if auto else "manual"}
    if auto:
        slit = _world_mm("视孔")
        lash = _world_mm("眼线配置")
        slit = slit[slit[:, 0] < 0]
        lash = lash[lash[:, 0] < 0]
        x0, x1 = slit[:, 0].min(), slit[:, 0].max()
        nb = int(math.ceil((x1 - x0) / VIEW_FIT_BIN_MM)) + 1
        sb = np.floor((slit[:, 0] - x0) / VIEW_FIT_BIN_MM).astype(int)
        slit_bottom = np.full(nb, np.inf)
        np.minimum.at(slit_bottom, sb, slit[:, 2])
        near = (lash[:, 0] >= x0 - 1) & (lash[:, 0] <= x1 + 1) & (lash[:, 2] > slit[:, 2].min() - 20)
        lb = np.clip(np.floor((lash[near, 0] - x0) / VIEW_FIT_BIN_MM).astype(int), 0, nb - 1)
        lash_top = np.full(nb, -np.inf)
        np.maximum.at(lash_top, lb, lash[near, 2])
        lash_top = np.maximum.reduce([lash_top, np.roll(lash_top, 1), np.roll(lash_top, -1)])  # 1-bin dilation
        ok = np.isfinite(slit_bottom) & np.isfinite(lash_top)
        if ok.any():
            need = float((lash_top[ok] - slit_bottom[ok]).max())
            dz = need + VIEW_FIT_MARGIN_MM
        else:
            dz = 0.0
            info["note"] = "睫毛件不在视孔下方，未调整"
    else:
        dz = manual_mm
    clamped = max(-10.0, min(10.0, dz))
    ob.location.z = base + clamped / 1000.0
    ob.update_tag()
    refresh()
    info.update({"offset_z_mm": round(clamped, 2), "clamped": clamped != dz})
    return info


def refresh(passes: int = 2) -> None:
    """Re-evaluate twice: chained cross-object drivers otherwise lag one update behind."""
    for _ in range(passes):
        for ob in bpy.data.objects:
            if ob.type == "MESH":
                ob.update_tag()
        bpy.context.view_layer.update()
        bpy.context.evaluated_depsgraph_get().update()


def measure_head() -> dict[str, Any]:
    """Evaluated outer size of the head shell (world mm)."""
    head = _obj(HEAD)
    dg = bpy.context.evaluated_depsgraph_get()
    ev = head.evaluated_get(dg)
    mesh = ev.to_mesh()
    try:
        mw = ev.matrix_world
        xs, ys, zs = [], [], []
        for v in mesh.vertices:
            w = mw @ v.co
            xs.append(w.x); ys.append(w.y); zs.append(w.z)
        n = len(mesh.vertices)
    finally:
        ev.to_mesh_clear()
    return {
        "width_mm": round((max(xs) - min(xs)) * 1000, 1),
        "depth_mm": round((max(ys) - min(ys)) * 1000, 1),
        "height_mm": round((max(zs) - min(zs)) * 1000, 1),
        "vertices": n,
        "wall_thickness_mm": round(head.modifiers["实体化"].thickness * 1000, 3),
    }
