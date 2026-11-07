"""Debug CLI: send a hand-written prompt (+ reference images) straight to one image backend.

Bypasses the app's prompt builder so prompts can be A/B-tested against Seedream (Ark) and Codex and
content-filter rejections can be read verbatim. Settings (keys, models, codex path) come from the repo
.env exactly like the backend.

    backend/.venv/Scripts/python backend/scripts/imagegen_debug.py spec.json [--only NAME] [--backend ark|codex]
                                                                  [--dry-run] [--skip-usage-check]

spec.json is one job or {"defaults": {...}, "jobs": [{...}, ...]}; a job is

    {
      "name": "clay-ark",                 # output folder name (default: job-<n>)
      "backend": "ark" | "codex",
      "prompt": "...",                    # or "prompt_file": "path.txt" (relative to the spec file)
      "images": ["ref.webp"],             # optional reference images (relative to the spec file)
      "repeat": 1,                        # run the same job n times (filter hit-rate checks)
      "ark": {"size": "1920x1280", "model": "...", "extra": {...}},   # extra = merged into the payload
      "codex": {"append_tool_requirement": true, "timeout_s": 900}
    }

Each run writes runtime/imagegen-debug/<timestamp>/<name>[-k]/: request.json (no key / no image data),
response.json or error.json, output images, and a summary.json for the whole batch.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(REPO_ROOT)  # Settings reads .env from the working directory

import httpx

from app.core.config import get_settings
from app.generation.backends.image_api import load_rgb, to_data_uri

OUT_ROOT = REPO_ROOT / "runtime" / "imagegen-debug"


def load_spec(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    defaults = raw.get("defaults", {}) if isinstance(raw, dict) and "jobs" in raw else {}
    jobs = raw["jobs"] if isinstance(raw, dict) and "jobs" in raw else [raw]
    out = []
    for i, job in enumerate(jobs, 1):
        merged = {**defaults, **job}
        for key in ("ark", "codex"):
            merged[key] = {**defaults.get(key, {}), **job.get(key, {})}
        merged.setdefault("name", f"job-{i}")
        if "prompt_file" in merged:
            merged["prompt"] = (path.parent / merged["prompt_file"]).read_text(encoding="utf-8")
        if not merged.get("prompt"):
            raise SystemExit(f"{merged['name']}: 缺少 prompt / prompt_file")
        merged["images"] = [str((path.parent / p).resolve()) for p in merged.get("images", [])]
        for p in merged["images"]:
            if not Path(p).is_file():
                raise SystemExit(f"{merged['name']}: 找不到参考图 {p}")
        out.append(merged)
    return out


def _error_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


async def run_ark(job: dict[str, Any], out: Path, dry_run: bool) -> dict[str, Any]:
    s = get_settings()
    cfg = job["ark"]
    payload: dict[str, Any] = {
        "model": cfg.get("model") or s.ark_image_model,
        "prompt": job["prompt"],
        "size": cfg.get("size") or s.ark_front_size,
        "output_format": "png",
        "response_format": "url",
        "watermark": False,
        **cfg.get("extra", {}),
    }
    images = [to_data_uri(load_rgb(Path(p)), int(s.ark_max_input_side)) for p in job["images"]]
    shown = {**payload, "image": [f"<{Path(p).name}>" for p in job["images"]] or None}
    (out / "request.json").write_text(json.dumps(shown, ensure_ascii=False, indent=2), encoding="utf-8")
    if dry_run:
        return {"status": "dry-run"}
    if images:
        payload["image"] = images[0] if len(images) == 1 else images
    if not s.ark_api_key.strip():
        return {"status": "error", "error": "ARK_API_KEY 未配置"}
    trace = uuid.uuid4().hex
    url = f"{s.ark_base_url.rstrip('/')}/images/generations"
    headers = {"Authorization": f"Bearer {s.ark_api_key.strip()}", "X-Client-Request-Id": trace}
    async with httpx.AsyncClient(timeout=float(s.ark_timeout_seconds)) as client:
        response = await client.post(url, headers=headers, json=payload)
        server_trace = response.headers.get("x-request-id") or response.headers.get("x-tt-logid") or trace
        if response.status_code != 200:
            body = _error_body(response)
            (out / "error.json").write_text(json.dumps({"http": response.status_code, "trace_id": server_trace,
                                                        "body": body}, ensure_ascii=False, indent=2), encoding="utf-8")
            err = body.get("error", body) if isinstance(body, dict) else body
            code = err.get("code") if isinstance(err, dict) else None
            msg = err.get("message") if isinstance(err, dict) else str(err)[:300]
            return {"status": "rejected" if response.status_code == 400 else "error", "http": response.status_code,
                    "code": code, "message": msg, "trace_id": server_trace}
        body = response.json()
        saved = []
        for i, item in enumerate(body.get("data", [])):
            if item.get("url"):
                data = (await client.get(item["url"])).content
            else:
                data = base64.b64decode(item["b64_json"])
            path = out / f"output-{i}.png"
            path.write_bytes(data)
            saved.append(path.name)
        slim = {k: v for k, v in body.items() if k != "data"}
        (out / "response.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"status": "ok", "files": saved, "trace_id": server_trace, "usage": body.get("usage")}


async def run_codex(job: dict[str, Any], out: Path, dry_run: bool, skip_usage: bool) -> dict[str, Any]:
    from app.generation.backends.codex import (
        _build_codex_command,
        _codex_failure_detail,
        _codex_tool_generated_images,
        _image_generation_tool_requirement,
    )
    from app.generation.codex_usage import ensure_codex_usage_allows_generation

    s = get_settings()
    cfg = job["codex"]
    prompt = job["prompt"]
    if cfg.get("append_tool_requirement", True):
        prompt = f"{prompt}\n\n{_image_generation_tool_requirement()}"
    workspace = out / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (out / "prompt.md").write_text(prompt, encoding="utf-8")
    command = _build_codex_command(s.codex_path, workspace, [Path(p) for p in job["images"]], prompt)
    (out / "request.json").write_text(json.dumps({"command": command[:-1] + ["<prompt.md>"]}, ensure_ascii=False,
                                                 indent=2), encoding="utf-8")
    if dry_run:
        return {"status": "dry-run"}
    if not skip_usage:
        try:
            await ensure_codex_usage_allows_generation(s)
        except Exception as exc:  # HTTPException from the usage gate  # noqa: BLE001 - best-effort debug batch keeps going
            return {"status": "error", "error": f"Codex 额度门槛: {getattr(exc, 'detail', exc)}"}
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.DEVNULL,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                   cwd=str(workspace))
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=float(cfg.get("timeout_s", 900)))
    except TimeoutError:
        process.kill()
        return {"status": "error", "error": "timeout"}
    (out / "codex-events.jsonl").write_bytes(stdout)
    (out / "codex-stderr.txt").write_bytes(stderr)
    images = _codex_tool_generated_images(stdout)
    saved = []
    for i, src in enumerate(images):
        dst = out / f"output-{i}{src.suffix.lower()}"
        shutil.copy2(src, dst)
        saved.append(dst.name)
    last = workspace / "last-message.txt"
    message = last.read_text(encoding="utf-8", errors="ignore").strip() if last.exists() else ""
    if saved:
        return {"status": "ok", "files": saved, "last_message": message[:500]}
    detail = _codex_failure_detail(stdout, stderr) if process.returncode else message[:500]
    return {"status": "rejected" if process.returncode == 0 else "error", "returncode": process.returncode,
            "message": detail}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", type=Path)
    ap.add_argument("--only", action="append", help="只跑指定 name（可多次）")
    ap.add_argument("--backend", choices=["ark", "codex"], help="覆盖所有 job 的 backend")
    ap.add_argument("--dry-run", action="store_true", help="只写 request.json，不调用")
    ap.add_argument("--skip-usage-check", action="store_true", help="跳过 Codex 额度门槛")
    args = ap.parse_args()

    jobs = load_spec(args.spec.resolve())
    if args.only:
        jobs = [j for j in jobs if j["name"] in args.only]
    batch = OUT_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")  # noqa: DTZ005 - local timestamp used as a debug output folder name
    summary = []
    for job in jobs:
        backend = args.backend or job.get("backend", "ark")
        for k in range(int(job.get("repeat", 1))):
            name = job["name"] if int(job.get("repeat", 1)) == 1 else f"{job['name']}-{k + 1}"
            out = batch / f"{name}-{backend}" if args.backend else batch / name
            out.mkdir(parents=True, exist_ok=True)
            started = time.monotonic()
            try:
                if backend == "ark":
                    result = await run_ark(job, out, args.dry_run)
                else:
                    result = await run_codex(job, out, args.dry_run, args.skip_usage_check)
            except Exception as exc:  # report and keep going with the batch  # noqa: BLE001 - report the job failure and continue the batch
                result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            result = {"name": name, "backend": backend, **result, "seconds": round(time.monotonic() - started, 1),
                      "dir": str(out)}
            summary.append(result)
            line = {k: v for k, v in result.items() if k not in ("dir", "last_message")}
            print(json.dumps(line, ensure_ascii=False), flush=True)
    batch.mkdir(parents=True, exist_ok=True)
    (batch / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"-> {batch}")
    return 0 if all(r["status"] in ("ok", "dry-run") for r in summary) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
