import mimetypes
import shutil
import uuid
from pathlib import Path

from httpx import AsyncClient

from app.core.config import get_settings
from app.images.media import IMAGE_MEDIA_TYPES, image_media_type, register_image_mime_types

FIXTURE_FILE = "kigurumi-candidate-1.webp"
FIXTURE_URL = f"/api/static/fixtures/{FIXTURE_FILE}"
ADMIN_AUDIT_PASSWORD = "change-me-admin-audit-password"


def fixture_path() -> Path:
    return Path(__file__).resolve().parents[1] / "app" / "static" / "fixtures" / FIXTURE_FILE


def test_image_media_types_cover_webp_and_register_mime_types() -> None:
    assert IMAGE_MEDIA_TYPES[".webp"] == "image/webp"
    assert image_media_type("candidate.WEBP") == "image/webp"
    assert image_media_type("candidate.png") == "image/png"
    assert image_media_type("candidate.jpeg") == "image/jpeg"
    assert image_media_type("candidate.txt") == "application/octet-stream"

    register_image_mime_types()
    assert mimetypes.guess_type("candidate.webp")[0] == "image/webp"
    assert mimetypes.guess_type("candidate.png")[0] == "image/png"
    assert mimetypes.guess_type("candidate.jpg")[0] == "image/jpeg"
    assert mimetypes.guess_type("candidate.jpeg")[0] == "image/jpeg"


async def test_fixture_webp_get_and_head_return_webp_mime(async_client: AsyncClient) -> None:
    get_response = await async_client.get(FIXTURE_URL)
    head_response = await async_client.head(FIXTURE_URL)

    assert get_response.status_code == 200
    assert head_response.status_code == 200
    assert get_response.headers["content-type"] == "image/webp"
    assert head_response.headers["content-type"] == "image/webp"
    assert get_response.content == fixture_path().read_bytes()


async def test_generated_webp_get_and_head_return_webp_mime(async_client: AsyncClient) -> None:
    generated_root = Path(get_settings().codex_output_dir)
    output_id = str(uuid.uuid4())
    output_dir = generated_root / "mime-test" / output_id / "outputs"
    output_dir.mkdir(parents=True)
    try:
        (output_dir / "candidate-1.webp").write_bytes(b"webp-bytes")

        url = f"/api/generated/mime-test/{output_id}/outputs/candidate-1.webp"
        get_response = await async_client.get(url)
        head_response = await async_client.head(url)

        assert get_response.status_code == 200
        assert head_response.status_code == 200
        assert get_response.headers["content-type"] == "image/webp"
        assert head_response.headers["content-type"] == "image/webp"
        assert get_response.content == b"webp-bytes"
    finally:
        shutil.rmtree(generated_root / "mime-test" / output_id, ignore_errors=True)


async def test_generated_assets_reject_non_images_traversal_and_missing_files(
    async_client: AsyncClient,
) -> None:
    generated_root = Path(get_settings().codex_output_dir)
    output_id = str(uuid.uuid4())
    output_dir = generated_root / "mime-test" / output_id / "outputs"
    output_dir.mkdir(parents=True)
    secret = generated_root.parent / f"secret-{output_id}.webp"
    secret.write_bytes(b"secret")
    try:
        (output_dir / "notes.txt").write_bytes(b"not an image")

        assert (
            await async_client.get(f"/api/generated/mime-test/{output_id}/outputs/notes.txt")
        ).status_code == 404
        assert (
            await async_client.get(f"/api/generated/mime-test/{output_id}/outputs/missing.webp")
        ).status_code == 404
        assert (
            await async_client.get(f"/api/generated/..%2F{secret.name}")
        ).status_code == 404
    finally:
        secret.unlink(missing_ok=True)
        shutil.rmtree(generated_root / "mime-test" / output_id, ignore_errors=True)


async def test_reference_asset_serves_webp_with_correct_mime(async_client: AsyncClient) -> None:
    reference_root = Path(get_settings().reference_upload_dir)
    upload_id = str(uuid.uuid4())
    upload_dir = reference_root / upload_id
    upload_dir.mkdir(parents=True)
    (upload_dir / "front.webp").write_bytes(b"reference-bytes")

    url = f"/api/references/references/{upload_id}/front.webp"
    response = await async_client.get(url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.content == b"reference-bytes"

    assert (
        await async_client.get(f"/api/references/references/{upload_id}/missing.webp")
    ).status_code == 404
    # The reference route keeps its existing 400 contract for malformed paths.
    assert (await async_client.get("/api/references/references/..%2F..%2Fsecret.webp")).status_code == 400


async def test_audit_reference_asset_serves_png_with_correct_mime(async_client: AsyncClient) -> None:
    reference_root = Path(get_settings().reference_upload_dir)
    upload_id = str(uuid.uuid4())
    upload_dir = reference_root / upload_id
    upload_dir.mkdir(parents=True)
    (upload_dir / "front.png").write_bytes(b"audit-png-bytes")

    login_response = await async_client.post(
        "/api/audit/login",
        json={"password": ADMIN_AUDIT_PASSWORD},
    )
    assert login_response.status_code == 200

    url = f"/api/audit/references/references/{upload_id}/front.png"
    response = await async_client.get(url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"audit-png-bytes"

    assert (
        await async_client.get(f"/api/audit/references/references/{upload_id}/missing.png")
    ).status_code == 404
    assert (await async_client.get("/api/audit/references/references/..%2F..%2Fsecret.png")).status_code == 404
