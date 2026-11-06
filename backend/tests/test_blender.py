import asyncio
import os
import sys
import textwrap
from pathlib import Path

import pytest
from httpx import AsyncClient

FAKE_BLENDER = textwrap.dedent(
    """
    import json, sys, time
    from pathlib import Path
    args = sys.argv[sys.argv.index("--") + 1:]
    params = Path(args[args.index("--params") + 1])
    out = Path(args[args.index("--out") + 1])
    mode = json.loads(params.read_text(encoding="utf-8"))["note"]
    print("VSK_PROGRESS 40 构建几何", flush=True)
    if mode == "slow":
        time.sleep(30)
    if mode == "crash":
        (out / "result.json").write_text(json.dumps({"ok": False, "error": "RuntimeError: boom"}), encoding="utf-8")
        sys.exit(1)
    (out / "render_front.png").write_bytes(b"\\x89PNG fake")
    (out / "front_shell.stl").write_bytes(b"solid fake")
    (out / "secret.txt").write_text("not listed", encoding="utf-8")
    result = {"ok": True, "warnings": ["front_shell 有 2 条非流形边"],
              "files": [{"kind": "render", "view": "front", "format": "png", "name": "render_front.png"},
                        {"kind": "model", "part": "front_shell", "format": "stl", "name": "front_shell.stl"}]}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print("VSK_PROGRESS 100 完成", flush=True)
    """
)


@pytest.fixture
def blender_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from app.blender import service
    from app.core.config import get_settings

    monkeypatch.setenv("BLENDER_OUTPUT_DIR", str(tmp_path / "blender"))
    get_settings.cache_clear()
    fake = tmp_path / "fake_blender.py"
    fake.write_text(FAKE_BLENDER, encoding="utf-8")
    manager = service.BlenderJobManager()
    monkeypatch.setattr(service, "blender_jobs", manager)
    monkeypatch.setattr("app.blender.router.blender_jobs", manager)
    monkeypatch.setattr(
        manager, "command",
        lambda job_id: [sys.executable, str(fake), "--", "--params",
                        str(manager.job_dir(job_id) / "params.json"), "--out", str(manager.job_dir(job_id))],
    )
    available = {"available": True, "reasons": [], "blender": "fake", "blend": "fake.blend"}
    monkeypatch.setattr("app.blender.router.availability", lambda: available)
    return manager


async def _wait(client: AsyncClient, job_id: str, timeout: float = 20) -> dict:
    for _ in range(int(timeout / 0.1)):
        job = (await client.get(f"/api/blender/jobs/{job_id}")).json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        await asyncio.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish: {job}")


async def test_schema_lists_params_and_presets(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/blender/schema")

    assert response.status_code == 200
    body = response.json()
    keys = {p["key"] for p in body["schema"]["params"]}
    assert {"base.size_tier", "view.spacing_mm", "face.eye_close", "structure.drain_holes"} <= keys
    assert {"standard", "lite", "extra", "example-wearer"} <= {p["id"] for p in body["presets"]}
    shell_params = [p for p in body["schema"]["params"] if p["group"] not in {"fit", "output"}]
    assert all("scales_with_shell" in p for p in shell_params)


async def test_validate_clamps_and_derives_scale(async_client: AsyncClient) -> None:
    response = await async_client.post("/api/blender/validate", json={"params": {
        "base.size_tier": "custom",
        "fit.head_circumference_mm": 640, "fit.head_width_mm": 180, "fit.head_height_mm": 235,
        "fit.ipd_mm": 70, "eye.width_scale": 9, "bogus.key": 1,
    }})

    assert response.status_code == 200
    body = response.json()
    assert body["params"]["eye.width_scale"] == 1.6
    assert "bogus.key" not in body["params"]
    assert any("eye.width_scale" in w for w in body["warnings"])
    derived = body["derived"]
    assert derived["required_mm"] == [245.2, 261.7, 282.6]
    assert derived["s"] == 1.08 and derived["recommended_tier"] == "extra"
    assert derived["view_spacing_mm"] == 70.0 and derived["view_spacing_source"] == "ipd"


async def test_validate_defaults_to_standard(async_client: AsyncClient) -> None:
    body = (await async_client.post("/api/blender/validate", json={})).json()

    assert body["derived"]["s"] == 1.0
    assert body["derived"]["meets"] is True
    assert body["warnings"] == []


async def test_standard_tier_warns_when_wearer_does_not_fit(async_client: AsyncClient) -> None:
    body = (await async_client.post("/api/blender/validate", json={"params": {
        "base.size_tier": "standard",
        "fit.head_circumference_mm": 640, "fit.head_width_mm": 180, "fit.head_height_mm": 235,
    }})).json()

    assert body["derived"]["meets"] is False
    assert any("不满足需求下限" in w for w in body["warnings"])


async def test_create_job_unavailable_without_blend(async_client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setenv("BLENDER_SHELL_BLEND", "")
    from app.core.config import get_settings

    get_settings.cache_clear()
    status = (await async_client.get("/api/blender/status")).json()
    response = await async_client.post("/api/blender/jobs", json={})

    assert status["available"] is False
    assert response.status_code == 503


async def test_job_runs_and_serves_listed_files(async_client: AsyncClient, blender_env) -> None:
    created = await async_client.post("/api/blender/jobs", json={"params": {"base.size_tier": "extra"}, "note": "ok"})
    assert created.status_code == 201
    job = await _wait(async_client, created.json()["id"])

    assert job["status"] == "succeeded", job
    assert job["progress"] == 100
    assert job["derived"]["s"] == 1.09
    assert "front_shell 有 2 条非流形边" in job["warnings"]
    base = f"/api/blender/jobs/{job['id']}/files"
    render = await async_client.get(f"{base}/render_front.png")
    model = await async_client.get(f"{base}/front_shell.stl")
    assert render.status_code == 200 and render.headers["content-type"] == "image/png"
    assert model.status_code == 200 and "attachment" in model.headers["content-disposition"]
    assert (await async_client.get(f"{base}/secret.txt")).status_code == 404
    assert (await async_client.get(f"{base}/..%2Fjob.json")).status_code == 404
    listed = (await async_client.get("/api/blender/jobs")).json()
    assert [j["id"] for j in listed] == [job["id"]]


async def test_job_failure_reports_blender_error(async_client: AsyncClient, blender_env) -> None:
    created = await async_client.post("/api/blender/jobs", json={"note": "crash"})
    job = await _wait(async_client, created.json()["id"])

    assert job["status"] == "failed"
    assert job["error"] == "RuntimeError: boom"


async def test_cancel_kills_running_build(async_client: AsyncClient, blender_env) -> None:
    created = await async_client.post("/api/blender/jobs", json={"note": "slow"})
    job_id = created.json()["id"]
    for _ in range(100):
        if (await async_client.get(f"/api/blender/jobs/{job_id}")).json()["status"] == "running":
            break
        await asyncio.sleep(0.05)
    await async_client.post(f"/api/blender/jobs/{job_id}/cancel")
    job = await _wait(async_client, job_id)

    assert job["status"] == "cancelled"


@pytest.mark.skipif(not os.environ.get("VSK_BLENDER_E2E"), reason="set VSK_BLENDER_E2E=1 plus BLENDER_PATH / BLENDER_SHELL_BLEND")
async def test_real_blender_build(async_client: AsyncClient, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BLENDER_OUTPUT_DIR", str(tmp_path / "blender"))
    from app.core.config import get_settings

    get_settings.cache_clear()
    created = await async_client.post("/api/blender/jobs", json={"params": {
        "output.views": ["front"], "output.render_px": 512, "output.export_formats": ["stl"], "output.parts": ["eyeline"],
    }})
    assert created.status_code == 201, created.text
    job = await _wait(async_client, created.json()["id"], timeout=300)

    assert job["status"] == "succeeded", job
    assert {f["name"] for f in job["result"]["files"]} == {"render_front.png", "eyeline.stl"}
    assert job["result"]["measured"]["wall_thickness_mm"] == 2.62
