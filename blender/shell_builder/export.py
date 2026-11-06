"""Export the printable parts (evaluated, booleans applied) as GLB / STL and check manifoldness."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import bmesh
import bpy

PARTS = {
    "front_shell": "前壳配置",
    "back_shell": "后壳配置",
    "eyeline": "眼线配置",
}


def _bake(object_name: str) -> bpy.types.Object:
    """Evaluated mesh of a part as a new temporary object (instances realised by the depsgraph)."""
    src = bpy.data.objects[object_name]
    dg = bpy.context.evaluated_depsgraph_get()
    ev = src.evaluated_get(dg)
    mesh = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=False, depsgraph=dg)
    tmp = bpy.data.objects.new(f"_vsk_export_{object_name}", mesh)
    tmp.matrix_world = ev.matrix_world
    bpy.context.scene.collection.objects.link(tmp)
    return tmp


def _mesh_report(ob: bpy.types.Object) -> dict[str, Any]:
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    try:
        non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
        boundary = sum(1 for e in bm.edges if e.is_boundary)
        dims = [round(d * 1000, 1) for d in ob.dimensions]
        return {
            "vertices": len(bm.verts),
            "faces": len(bm.faces),
            "non_manifold_edges": non_manifold,
            "boundary_edges": boundary,
            "manifold": non_manifold == 0,
            "size_mm": dims,
        }
    finally:
        bm.free()


def _select_only(ob: bpy.types.Object) -> None:
    for other in bpy.context.view_layer.objects:
        other.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob


def export_parts(out_dir: Path, parts: list[str], formats: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    files: list[dict[str, Any]] = []
    warnings: list[str] = []
    for part in parts:
        tmp = _bake(PARTS[part])
        try:
            report = _mesh_report(tmp)
            if report["vertices"] == 0:
                warnings.append(f"{part} 为空网格，已跳过导出")
                continue
            if not report["manifold"]:
                warnings.append(f"{part} 有 {report['non_manifold_edges']} 条非流形边（打印前请检查）")
            _select_only(tmp)
            if "stl" in formats:
                path = out_dir / f"{part}.stl"
                # STL is unitless; slicers read it as millimetres
                bpy.ops.wm.stl_export(filepath=str(path), export_selected_objects=True,
                                      global_scale=1000.0, apply_modifiers=False, ascii_format=False)
                files.append({"part": part, "format": "stl", "name": path.name, **report})
            if "glb" in formats:
                path = out_dir / f"{part}.glb"
                bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True,
                                          export_apply=False, export_materials="NONE")
                files.append({"part": part, "format": "glb", "name": path.name, **report})
        finally:
            mesh = tmp.data
            bpy.data.objects.remove(tmp)
            bpy.data.meshes.remove(mesh)
    for entry in files:
        path = out_dir / entry["name"]
        entry["bytes"] = path.stat().st_size if path.exists() else 0
    return files, warnings
