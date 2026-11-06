"""`blender_build` jobs: run blender/build_shell.py headless, track progress, expose the outputs.

Kept separate from the image-generation job store: a build is one subprocess with a working
directory (runtime/blender/<job id>/) holding params.json, result.json, renders and models.
The subprocess runs in a worker thread (portable across event-loop policies) and can be killed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.blender.schema import ParamSnapshot, ValidatedParams, scripts_dir
from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.core.time import utc_now

logger = logging.getLogger("uvicorn.error")

TERMINAL = {"succeeded", "failed", "cancelled"}
SERVED_SUFFIXES = {".png": "image/png", ".glb": "model/gltf-binary", ".stl": "model/stl", ".json": "application/json"}
LOG_TAIL_LINES = 40


@dataclass
class BlenderJob:
    id: str
    status: str
    progress: int
    phase_label: str
    created_at: str
    snapshot: dict[str, Any]
    params: dict[str, Any]
    derived: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    finished_at: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    log_tail: list[str] = field(default_factory=list)


def availability() -> dict[str, Any]:
    settings = get_settings()
    reasons: list[str] = []
    blend = resolve_repo_path(settings.blender_shell_blend) if settings.blender_shell_blend else None
    if blend is None:
        reasons.append("未设置 BLENDER_SHELL_BLEND（基座 .blend 路径）")
    elif not blend.is_file():
        reasons.append(f"基座 .blend 不存在：{blend}")
    executable = shutil.which(settings.blender_path) or (
        settings.blender_path if Path(settings.blender_path).is_file() else None
    )
    if executable is None:
        reasons.append(f"找不到 Blender 可执行文件：{settings.blender_path}")
    script = scripts_dir() / "build_shell.py"
    if not script.is_file():
        reasons.append(f"找不到构建脚本：{script}")
    return {
        "available": not reasons,
        "reasons": reasons,
        "blender": executable,
        "blend": str(blend) if blend else None,
    }


class BlenderJobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, BlenderJob] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.RLock()
        self._semaphore: asyncio.Semaphore | None = None

    # ---- paths / persistence -------------------------------------------------------------
    @staticmethod
    def root() -> Path:
        return resolve_repo_path(get_settings().blender_output_dir)

    def job_dir(self, job_id: str) -> Path:
        return self.root() / job_id

    def _save(self, job: BlenderJob) -> None:
        directory = self.job_dir(job.id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "job.json").write_text(json.dumps(asdict(job), ensure_ascii=False), encoding="utf-8")

    def _update(self, job_id: str, **changes: Any) -> BlenderJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            for key, value in changes.items():
                setattr(job, key, value)
            self._save(job)
            return job

    # ---- public API ----------------------------------------------------------------------
    def create(self, snapshot: ParamSnapshot, validated: ValidatedParams) -> BlenderJob:
        job = BlenderJob(
            id=str(uuid.uuid4()),
            status="queued",
            progress=0,
            phase_label="排队中",
            created_at=utc_now().isoformat(),
            snapshot=snapshot.model_dump(),
            params=validated.params,
            derived=validated.derived,
            warnings=list(validated.warnings),
        )
        directory = self.job_dir(job.id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "params.json").write_text(
            json.dumps({"schema_version": validated.schema_version, "params": validated.params,
                        "source": snapshot.source, "note": snapshot.note}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with self._lock:
            self._jobs[job.id] = job
            self._save(job)
        return job

    def submit(self, job_id: str) -> None:
        asyncio.get_running_loop().create_task(self.run(job_id))

    def get(self, job_id: str) -> BlenderJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None:
            return job
        path = self.job_dir(job_id) / "job.json" if _safe_id(job_id) else None
        if path is None or not path.is_file():
            return None
        try:
            job = BlenderJob(**json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None
        if job.status not in TERMINAL:  # the server restarted while it was running
            job.status, job.error = "failed", "服务重启，任务中断"
        with self._lock:
            self._jobs[job.id] = job
        return job

    def list(self, limit: int = 20) -> list[BlenderJob]:
        root = self.root()
        ids = []
        if root.is_dir():
            ids = [p.name for p in root.iterdir() if (p / "job.json").is_file()]
        jobs = [job for job_id in ids if (job := self.get(job_id)) is not None]
        jobs.sort(key=lambda job: job.created_at, reverse=True)
        return jobs[:limit]

    def cancel(self, job_id: str) -> BlenderJob | None:
        job = self.get(job_id)
        if job is None or job.status in TERMINAL:
            return job
        with self._lock:
            self._cancelled.add(job_id)
            proc = self._procs.get(job_id)
        if proc is not None and proc.poll() is None:
            proc.kill()
        if job.status == "queued":
            return self._update(job_id, status="cancelled", phase_label="已取消", finished_at=utc_now().isoformat())
        return job

    def file_path(self, job_id: str, name: str) -> Path | None:
        job = self.get(job_id)
        if job is None or not _safe_name(name):
            return None
        allowed = {"params.json", "result.json"}
        if job.result:
            allowed |= {entry.get("name") for entry in job.result.get("files", []) if entry.get("name")}
        if name not in allowed or Path(name).suffix.lower() not in SERVED_SUFFIXES:
            return None
        path = self.job_dir(job_id) / name
        return path if path.is_file() else None

    # ---- execution -----------------------------------------------------------------------
    async def run(self, job_id: str) -> None:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(max(1, get_settings().blender_parallelism))
        async with self._semaphore:
            if job_id in self._cancelled:
                return
            await asyncio.to_thread(self._run_blocking, job_id)

    def command(self, job_id: str) -> list[str]:
        settings = get_settings()
        directory = self.job_dir(job_id)
        return [
            shutil.which(settings.blender_path) or settings.blender_path,
            "-b",
            "--factory-startup",
            str(resolve_repo_path(settings.blender_shell_blend)),
            "--python-exit-code",
            "1",
            "-P",
            str(scripts_dir() / "build_shell.py"),
            "--",
            "--params",
            str(directory / "params.json"),
            "--out",
            str(directory),
        ]

    def _run_blocking(self, job_id: str) -> None:
        settings = get_settings()
        directory = self.job_dir(job_id)
        self._update(job_id, status="running", progress=5, phase_label="启动 Blender")
        started = time.monotonic()
        tail: list[str] = []
        try:
            proc = subprocess.Popen(
                self.command(job_id),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(directory),
            )
        except OSError as exc:
            self._finish(job_id, "failed", error=f"无法启动 Blender：{exc}")
            return
        with self._lock:
            self._procs[job_id] = proc
        timer = threading.Timer(settings.blender_timeout_seconds, proc.kill)
        timer.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                tail = (tail + [line])[-LOG_TAIL_LINES:]
                if line.startswith("VSK_PROGRESS "):
                    parts = line.split(" ", 2)
                    try:
                        percent = int(parts[1])
                    except (IndexError, ValueError):
                        continue
                    label = parts[2] if len(parts) > 2 else ""
                    self._update(job_id, progress=min(99, max(5, percent)), phase_label=label)
            code = proc.wait()
        finally:
            timer.cancel()
            with self._lock:
                self._procs.pop(job_id, None)
        elapsed = time.monotonic() - started
        result = _read_json(directory / "result.json")
        if job_id in self._cancelled:
            self._finish(job_id, "cancelled", result=result, tail=tail)
        elif elapsed >= settings.blender_timeout_seconds and code != 0:
            self._finish(job_id, "failed", error=f"构建超时（>{settings.blender_timeout_seconds}s）", result=result, tail=tail)
        elif code == 0 and result and result.get("ok"):
            self._finish(job_id, "succeeded", result=result, tail=tail)
        else:
            error = (result or {}).get("error") or f"Blender 退出码 {code}"
            self._finish(job_id, "failed", error=error, result=result, tail=tail)
        logger.info("Blender build finished job_id=%s code=%s elapsed=%.1fs", job_id, code, elapsed)

    def _finish(self, job_id: str, status: str, *, error: str | None = None,
                result: dict[str, Any] | None = None, tail: list[str] | None = None) -> None:
        job = self.get(job_id)
        warnings = list(job.warnings) if job else []
        if result:
            warnings = list(dict.fromkeys(warnings + list(result.get("warnings", []))))
            result = {k: v for k, v in result.items() if k != "traceback"}
        labels = {"succeeded": "构建完成", "failed": "构建失败", "cancelled": "已取消"}
        self._update(
            job_id,
            status=status,
            progress=100 if status == "succeeded" else (job.progress if job else 0),
            phase_label=labels[status],
            finished_at=utc_now().isoformat(),
            error=error,
            result=result,
            warnings=warnings,
            log_tail=tail or [],
        )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _safe_id(job_id: str) -> bool:
    try:
        return str(uuid.UUID(job_id)) == job_id
    except ValueError:
        return False


def _safe_name(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and name not in {".", ".."} and not name.startswith(".")


blender_jobs = BlenderJobManager()
