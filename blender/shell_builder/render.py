"""Preview renders (Workbench, orthographic) of the head as it is shown in the base file."""
from __future__ import annotations

import math
from pathlib import Path

import bpy
from mathutils import Vector

# view name → camera direction (from the head centre towards the camera; the face looks +Y)
VIEWS = {
    "front": Vector((0.0, 1.0, 0.0)),
    "three_quarter": Vector((-math.sin(math.radians(40)), math.cos(math.radians(40)), 0.0)),
    "side": Vector((-1.0, 0.0, 0.0)),
    "back": Vector((0.0, -1.0, 0.0)),
}


def _render_bounds() -> tuple[Vector, float]:
    dg = bpy.context.evaluated_depsgraph_get()
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    for ob in bpy.context.scene.objects:
        if ob.type != "MESH" or ob.hide_render or not ob.visible_get():
            continue
        ev = ob.evaluated_get(dg)
        for corner in ev.bound_box:
            w = ev.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    if not math.isfinite(lo.x):
        raise RuntimeError("没有可渲染的对象")
    return (lo + hi) / 2, max(hi - lo)


# flat eye decals that do not follow eye closing; hidden when the eyes are mostly closed
EYE_DECALS = ("眼白", "眼珠")


def render_views(out_dir: Path, views: list[str], px: int, *, eyes_closed: bool = False) -> list[Path]:
    scene = bpy.context.scene
    for name in EYE_DECALS:
        decal = bpy.data.objects.get(name)
        if decal is not None and eyes_closed:
            decal.hide_render = True
    scene.render.engine = "BLENDER_WORKBENCH"
    shading = scene.display.shading
    shading.light = "STUDIO"
    shading.color_type = "SINGLE"
    shading.single_color = (0.82, 0.80, 0.78)
    shading.show_cavity = True
    shading.cavity_type = "WORLD"
    scene.render.resolution_x = px
    scene.render.resolution_y = px
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"

    centre, size = _render_bounds()
    cam_data = bpy.data.cameras.new("_vsk_build_cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = size * 1.12
    cam_data.clip_start = 0.001
    cam_data.clip_end = size * 10
    cam = bpy.data.objects.new("_vsk_build_cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    written = []
    try:
        for name in views:
            direction = VIEWS[name]
            cam.location = centre + direction * size * 3
            cam.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
            path = out_dir / f"render_{name}.png"
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            written.append(path)
    finally:
        bpy.data.objects.remove(cam)
        bpy.data.cameras.remove(cam_data)
    return written
