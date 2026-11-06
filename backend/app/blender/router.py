"""3D head-shell API: parameter registry, validation, `blender_build` jobs and their outputs."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.blender.schema import (
    BlenderSchemaUnavailable,
    ParamSnapshot,
    ValidatedParams,
    load_presets,
    load_schema,
    validate_snapshot,
)
from app.blender.service import SERVED_SUFFIXES, BlenderJob, availability, blender_jobs

router = APIRouter(prefix="/blender", tags=["blender"])


class BlenderJobOut(BaseModel):
    id: str
    status: str
    progress: int
    phase_label: str
    created_at: str
    finished_at: str | None
    params: dict[str, Any]
    derived: dict[str, Any]
    warnings: list[str]
    error: str | None
    result: dict[str, Any] | None
    note: str
    source: str


def _out(job: BlenderJob) -> BlenderJobOut:
    return BlenderJobOut(
        id=job.id,
        status=job.status,
        progress=job.progress,
        phase_label=job.phase_label,
        created_at=job.created_at,
        finished_at=job.finished_at,
        params=job.params,
        derived=job.derived,
        warnings=job.warnings,
        error=job.error,
        result=job.result,
        note=str(job.snapshot.get("note", "")),
        source=str(job.snapshot.get("source", "manual")),
    )


def _schema_or_503() -> dict[str, Any]:
    try:
        return load_schema()
    except BlenderSchemaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/status")
async def blender_status() -> dict[str, Any]:
    return availability()


@router.get("/schema")
async def blender_schema() -> dict[str, Any]:
    schema = _schema_or_503()
    return {"schema": schema, "presets": load_presets()}


@router.post("/validate", response_model=ValidatedParams)
async def blender_validate(snapshot: ParamSnapshot) -> ValidatedParams:
    _schema_or_503()
    return validate_snapshot(snapshot)


@router.post("/jobs", response_model=BlenderJobOut, status_code=201)
async def create_blender_job(snapshot: ParamSnapshot) -> BlenderJobOut:
    status = availability()
    if not status["available"]:
        raise HTTPException(status_code=503, detail="；".join(status["reasons"]))
    _schema_or_503()
    job = blender_jobs.create(snapshot, validate_snapshot(snapshot))
    blender_jobs.submit(job.id)
    return _out(job)


@router.get("/jobs", response_model=list[BlenderJobOut])
async def list_blender_jobs(limit: int = 20) -> list[BlenderJobOut]:
    return [_out(job) for job in blender_jobs.list(max(1, min(limit, 100)))]


@router.get("/jobs/{job_id}", response_model=BlenderJobOut)
async def get_blender_job(job_id: str) -> BlenderJobOut:
    job = blender_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Blender job not found")
    return _out(job)


@router.post("/jobs/{job_id}/cancel", response_model=BlenderJobOut)
async def cancel_blender_job(job_id: str) -> BlenderJobOut:
    job = blender_jobs.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Blender job not found")
    return _out(job)


@router.api_route("/jobs/{job_id}/files/{name}", methods=["GET", "HEAD"])
async def get_blender_file(job_id: str, name: str) -> FileResponse:
    path = blender_jobs.file_path(job_id, name)
    if path is None:
        raise HTTPException(status_code=404, detail="File not found")
    media_type = SERVED_SUFFIXES[path.suffix.lower()]
    download = path.suffix.lower() in {".glb", ".stl"}
    return FileResponse(path, media_type=media_type, filename=path.name if download else None)
