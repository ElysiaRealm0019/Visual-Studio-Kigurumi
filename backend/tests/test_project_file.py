"""Version PNG download and .vkp project-file export/import."""

import io
import json
import zipfile
from pathlib import Path

from PIL import Image

from app.agent.store import conversation_store
from tests.test_agent import agent_env, auto_front_policy, send, start_conversation, use_llm  # noqa: F401


def png_bytes(color=(10, 200, 30), size=(64, 80)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


async def test_version_downloads_as_png(agent_env, async_client, monkeypatch):
    use_llm(monkeypatch, auto_front_policy)
    conversation_id = await start_conversation(async_client)
    await send(async_client, conversation_id, files=1)

    response = await async_client.get(f"/agent/conversations/{conversation_id}/versions/front-1/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert 'filename="front-1.png"' in response.headers["content-disposition"]
    assert Image.open(io.BytesIO(response.content)).format == "PNG"

    missing = await async_client.get(f"/agent/conversations/{conversation_id}/versions/nope/download")
    assert missing.status_code == 404


async def test_export_import_round_trip(agent_env, async_client, monkeypatch):
    use_llm(monkeypatch, auto_front_policy)
    conversation_id = await start_conversation(async_client)
    await send(async_client, conversation_id, files=1)
    original = (await async_client.get(f"/agent/conversations/{conversation_id}")).json()

    export = await async_client.get(f"/agent/conversations/{conversation_id}/export")
    assert export.status_code == 200
    assert export.headers["content-type"] == "application/zip"
    assert export.headers["content-disposition"].startswith('attachment; filename="')
    names = zipfile.ZipFile(io.BytesIO(export.content)).namelist()
    assert "vsk-project.json" in names and "project.json" in names
    # Fixture-provider outputs live in the shared fixtures dir instead of runtime/generated, so only the
    # assets (uploads + clean version pixels) are guaranteed here.
    assert any(name.startswith("assets/") for name in names)

    imported = await async_client.post(
        "/agent/conversations/import",
        files={"file": ("project.vkp", export.content, "application/zip")},
    )
    assert imported.status_code == 200, imported.text
    body = imported.json()
    new_id = body["id"]
    assert new_id != conversation_id
    assert body["title"] == original["title"] and body["locale"] == original["locale"]
    assert len(body["state"]["images"]) == len(original["state"]["images"])
    assert len(body["state"]["references"]) == len(original["state"]["references"])

    # Pixels were restored under the new id: the editor source and the public file both resolve.
    source = await async_client.get(f"/agent/conversations/{new_id}/versions/front-1/source")
    assert source.status_code == 200
    download = await async_client.get(f"/agent/conversations/{new_id}/versions/front-1/download")
    assert download.status_code == 200
    reference = body["state"]["references"][0]["url"]
    reference_response = await async_client.get(reference.removeprefix("/api"))
    assert new_id in reference and reference_response.status_code == 200

    listing = (await async_client.get("/agent/conversations")).json()
    assert {project["id"] for project in listing} >= {conversation_id, new_id}


async def test_export_unknown_conversation_404(agent_env, async_client):
    response = await async_client.get("/agent/conversations/00000000-0000-0000-0000-000000000000/export")
    assert response.status_code == 404


async def test_import_rejects_invalid_files(agent_env, async_client):
    def send_file(name: str, payload: bytes):
        return async_client.post("/agent/conversations/import", files={"file": (name, payload, "application/zip")})

    assert (await send_file("x.zip", b"garbage")).status_code == 400  # wrong suffix
    assert (await send_file("x.vkp", b"garbage")).status_code == 400  # not a zip

    no_manifest = io.BytesIO()
    with zipfile.ZipFile(no_manifest, "w") as bundle:
        bundle.writestr("other.txt", "hi")
    assert (await send_file("x.vkp", no_manifest.getvalue())).status_code == 400

    wrong_format = io.BytesIO()
    with zipfile.ZipFile(wrong_format, "w") as bundle:
        bundle.writestr("vsk-project.json", json.dumps({"format": "something-else", "version": 1}))
        bundle.writestr("project.json", "{}")
    assert (await send_file("x.vkp", wrong_format.getvalue())).status_code == 400

    traversal = io.BytesIO()
    with zipfile.ZipFile(traversal, "w") as bundle:
        bundle.writestr("vsk-project.json", json.dumps({"format": "vsk-project", "version": 1}))
        bundle.writestr("project.json", json.dumps({"id": "innocent", "state": {}}))
        bundle.writestr("assets/../evil.txt", "boom")
    assert (await send_file("x.vkp", traversal.getvalue())).status_code == 400

    # Nothing was written by the rejected imports.
    assert conversation_store.list() == []
