"""Deterministic head-shell build: params.json → renders + GLB/STL + result.json.

    blender -b --factory-startup ProjectPulsar_Base.blend --python-exit-code 1 \
        -P blender/build_shell.py -- --params out/params.json --out out/

`--factory-startup` keeps the user's add-ons out of the build.

The base .blend is opened read-only (never saved). Progress lines on stdout have the form
`VSK_PROGRESS <percent> <label>` for the backend job runner.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import bpy  # noqa: E402

from shell_builder import sizing  # noqa: E402


def progress(percent: int, label: str) -> None:
    print(f"VSK_PROGRESS {percent} {label}", flush=True)


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="build_shell.py")
    parser.add_argument("--params", required=True, help="参数快照 JSON（{schema_version, params} 或纯 params）")
    parser.add_argument("--out", required=True, help="产物目录")
    parser.add_argument("--schema", default=str(sizing.SCHEMA_PATH))
    parser.add_argument("--no-render", action="store_true")
    parser.add_argument("--no-export", action="store_true")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    timings: dict[str, float] = {}
    result: dict = {
        "ok": False,
        "blender_version": bpy.app.version_string,
        "blend_file": bpy.data.filepath,
        "warnings": [],
        "files": [],
    }

    def lap(name: str, t0: float) -> float:
        now = time.monotonic()
        timings[name] = round(now - t0, 2)
        return now

    try:
        if not bpy.data.filepath:
            raise RuntimeError("未打开基座 .blend：请以 `blender -b <ProjectPulsar_Base.blend> -P build_shell.py -- ...` 调用")
        schema = sizing.load_schema(args.schema)
        snapshot = json.loads(Path(args.params).resolve().read_text(encoding="utf-8"))
        raw = snapshot.get("params", snapshot) if isinstance(snapshot, dict) else {}
        version = snapshot.get("schema_version") if isinstance(snapshot, dict) else None
        if version is not None and version != schema["schema_version"]:
            result["warnings"].append(f"快照 schema_version={version} 与当前 {schema['schema_version']} 不一致，按当前 schema 校验")
        params, warnings = sizing.validate(schema, raw)
        derived = sizing.resolve(schema, params)
        result["warnings"] += warnings + derived["notes"]
        result["schema_version"] = schema["schema_version"]
        result["params"] = params
        result["scale"] = derived

        from shell_builder import apply, export, render

        t = time.monotonic()
        progress(10, "写入参数")
        applied = apply.apply_params(params, derived, final_booleans=not args.no_export)
        slit = applied["view_slit"]
        result["view_slit"] = slit
        if slit["clamped"]:
            result["warnings"].append(f"视孔升降超出 ±10mm，已截断为 {slit['offset_z_mm']}mm")
        elif slit["mode"] == "auto" and abs(slit["offset_z_mm"]) > apply.VIEW_FIT_WARN_MM:
            result["warnings"].append(f"视孔自动上下偏移 {slit['offset_z_mm']}mm，可能偏离佩戴者眼高，请用顶部垫层配合或改为手动")
        progress(40, "构建几何")
        result["measured"] = apply.measure_head()
        t = lap("geometry", t)

        if not args.no_render:
            progress(55, "渲染")
            for path in render.render_views(out_dir, params["output.views"], int(params["output.render_px"]),
                                                 eyes_closed=params["face.eye_close"] > 0.5):
                result["files"].append({"kind": "render", "view": path.stem.removeprefix("render_"),
                                        "format": "png", "name": path.name, "bytes": path.stat().st_size})
            t = lap("render", t)

        if not args.no_export and params["output.parts"] and params["output.export_formats"]:
            progress(75, "导出模型")
            files, warns = export.export_parts(out_dir, params["output.parts"], params["output.export_formats"])
            result["files"] += [{"kind": "model", **f} for f in files]
            result["warnings"] += warns
            t = lap("export", t)

        result["ok"] = True
        progress(100, "完成")
    except Exception as exc:  # report every failure through result.json
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
        print(result["traceback"], file=sys.stderr, flush=True)
    finally:
        result["timings_s"] = {**timings, "total": round(time.monotonic() - started, 2)}
        (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    # `blender -P` ignores the script's return value; exit explicitly so callers see failures
    sys.exit(code)
