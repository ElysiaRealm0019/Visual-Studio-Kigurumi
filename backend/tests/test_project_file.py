"""Version PNG download and .vkp project-file export/import."""

import io
import json
import zipfile
from pathlib import Path, PurePosixPath

from PIL import Image

from app.agent.store import conversation_store
from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from tests.test_agent import (  # noqa: F401
    agent_env,
    auto_front_policy,
    send,
    start_conversation,
    use_llm,
)

FIXTURE_STATIC_PREFIX = "/api/static/fixtures/"


def png_bytes(color=(10, 200, 30), size=(64, 80)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def generated_asset_path(url: str, session_id: str) -> Path:
    """The public /api/generated/<session>/... url of a real output resolved back to its file on disk."""
    prefix = get_settings().generated_public_prefix.rstrip("/") + "/"
    assert url.startswith(prefix + session_id + "/"), url
    relative = PurePosixPath(url.removeprefix(prefix))
    assert relative.parts[:1] == (session_id,), url
    return resolve_repo_path(get_settings().codex_output_dir) / Path(*relative.parts)


def asset_bytes_for_url(url: str, session_id: str) -> bytes:
    """Bytes behind an image url: real generated outputs come from the session dir, fixture ones from app/static."""
    if url.startswith(get_settings().generated_public_prefix.rstrip("/") + "/"):
        return generated_asset_path(url, session_id).read_bytes()
    assert url.startswith(FIXTURE_STATIC_PREFIX), url
    fixture_dir = Path(get_settings().fixture_dir)
    if not fixture_dir.is_absolute():
        # Same resolution as app/main.py: a relative fixture dir lives under backend/.
        fixture_dir = Path(__file__).resolve().parent.parent / fixture_dir
    return (fixture_dir / url.removeprefix(FIXTURE_STATIC_PREFIX)).read_bytes()


async def test_version_downloads_as_png(agent_env, async_client, monkeypatch):  # noqa: F811
    use_llm(monkeypatch, auto_front_policy)
    conversation_id = await start_conversation(async_client)
    await send(async_client, conversation_id, files=1)

    response = await async_client.get(f"/api/agent/conversations/{conversation_id}/versions/front-1/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert 'filename="front-1.png"' in response.headers["content-disposition"]
    assert Image.open(io.BytesIO(response.content)).format == "PNG"

    missing = await async_client.get(f"/api/agent/conversations/{conversation_id}/versions/nope/download")
    assert missing.status_code == 404


async def test_export_import_round_trip(agent_env, async_client, monkeypatch):  # noqa: F811
    use_llm(monkeypatch, auto_front_policy)
    conversation_id = await start_conversation(async_client)
    await send(async_client, conversation_id, files=1)
    original = (await async_client.get(f"/api/agent/conversations/{conversation_id}")).json()

    export = await async_client.get(f"/api/agent/conversations/{conversation_id}/export")
    assert export.status_code == 200
    assert export.headers["content-type"] == "application/zip"
    assert export.headers["content-disposition"].startswith('attachment; filename="')
    names = zipfile.ZipFile(io.BytesIO(export.content)).namelist()
    assert "vsk-project.json" in names and "project.json" in names
    # Fixture-provider outputs live in the shared fixtures dir instead of runtime/generated, so only the
    # assets (uploads + clean version pixels) are guaranteed here.
    assert any(name.startswith("assets/") for name in names)

    imported = await async_client.post(
        "/api/agent/conversations/import",
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
    source = await async_client.get(f"/api/agent/conversations/{new_id}/versions/front-1/source")
    assert source.status_code == 200
    download = await async_client.get(f"/api/agent/conversations/{new_id}/versions/front-1/download")
    assert download.status_code == 200
    reference = body["state"]["references"][0]["url"]
    reference_response = await async_client.get(reference)
    assert new_id in reference and reference_response.status_code == 200

    listing = (await async_client.get("/api/agent/conversations")).json()
    assert {project["id"] for project in listing} >= {conversation_id, new_id}


async def test_export_import_carries_generated_outputs(agent_env, async_client, monkeypatch):  # noqa: F811
    use_llm(monkeypatch, auto_front_policy)
    conversation_id = await start_conversation(async_client)
    project = await send(async_client, conversation_id, files=1)
    session_id = conversation_store.get(conversation_id).state.character_session_id
    base_id = project["state"]["images"][0]["id"]

    # A saved editor version is a real generated output: pixels under runtime/generated/<session>/ with the public
    # /api/generated/<session>/... url the editor renders, the same layout Codex and bridge results use.
    saved = await async_client.post(
        f"/api/agent/conversations/{conversation_id}/versions",
        data={"base_image_id": base_id, "note": "generated export", "source": "manual"},
        files={"image": ("edited.png", png_bytes(color=(200, 10, 30)), "image/png")},
    )
    assert saved.status_code == 200, saved.text
    project = saved.json()
    prefix = get_settings().generated_public_prefix.rstrip("/") + "/"
    generated = [image for image in project["state"]["images"] if image["url"].startswith(prefix)]
    assert len(generated) == 1
    generated_url = generated[0]["url"]
    generated_source = generated_asset_path(generated_url, session_id)
    assert generated_source.is_file()
    served = await async_client.get(generated_url)
    assert served.status_code == 200
    assert served.content == generated_source.read_bytes()

    export = await async_client.get(f"/api/agent/conversations/{conversation_id}/export")
    assert export.status_code == 200
    bundle = zipfile.ZipFile(io.BytesIO(export.content))
    names = bundle.namelist()
    entry = "generated/" + generated_source.relative_to(
        resolve_repo_path(get_settings().codex_output_dir) / session_id
    ).as_posix()
    assert entry in names
    assert bundle.read(entry) == generated_source.read_bytes()

    imported = await async_client.post(
        "/api/agent/conversations/import",
        files={"file": ("project.vkp", export.content, "application/zip")},
    )
    assert imported.status_code == 200, imported.text
    copy = imported.json()
    new_id = copy["id"]
    assert new_id != conversation_id
    new_session = conversation_store.get(new_id).state.character_session_id
    assert new_session == new_id
    old_urls = {image["url"] for image in project["state"]["images"]}

    for image in copy["state"]["images"]:
        url = image["url"]
        response = await async_client.get(url)
        assert response.status_code == 200, url
        assert response.content == asset_bytes_for_url(url, new_session), url
        if url.startswith(prefix):
            # The copy's url points at the new session dir the import restored the pixels into.
            assert url not in old_urls and new_id in url and url == generated_url.replace(session_id, new_id)

    # The project the bundle came from keeps serving its own generated output untouched.
    for url in old_urls:
        response = await async_client.get(url)
        assert response.status_code == 200, url
        assert response.content == asset_bytes_for_url(url, session_id), url


async def test_export_unknown_conversation_404(agent_env, async_client):  # noqa: F811
    response = await async_client.get("/api/agent/conversations/00000000-0000-0000-0000-000000000000/export")
    assert response.status_code == 404


async def test_import_rejects_invalid_files(agent_env, async_client):  # noqa: F811
    def send_file(name: str, payload: bytes):
        return async_client.post("/api/agent/conversations/import", files={"file": (name, payload, "application/zip")})

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
