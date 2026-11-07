from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent.router import router as agent_router
from app.album.router import router as album_router
from app.audit.router import router as audit_router
from app.blender.router import router as blender_router
from app.core.config import get_settings
from app.generation.router import router as generation_router
from app.images.media import image_media_type, register_image_mime_types, resolve_image_asset
from app.prompts.router import router as prompts_router
from app.references.router import router as references_router
from app.settings.router import router as settings_router


def create_app() -> FastAPI:
    register_image_mime_types()
    settings = get_settings()
    app = FastAPI(title="Kig Preview API", version="0.1.0")
    cors_allowed_origins = _csv_values(settings.cors_allowed_origins)
    if cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization"],
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.app_env}

    fixture_dir = Path(settings.fixture_dir)
    if not fixture_dir.is_absolute():
        fixture_dir = Path(__file__).resolve().parent.parent / fixture_dir
    fixture_static_kwargs = {"directory": str(fixture_dir), "check_dir": False}
    app.mount(
        "/api/static/fixtures",
        StaticFiles(**fixture_static_kwargs),
        name="api_fixture_static",
    )
    generated_prefixes = _generated_public_prefixes(settings.generated_public_prefix)

    async def generated_asset(asset_path: str) -> FileResponse:
        file_path = resolve_image_asset(
            asset_path,
            settings.codex_output_dir,
            invalid_detail="Generated asset not found",
            missing_detail="Generated asset not found",
        )
        return FileResponse(file_path, media_type=image_media_type(file_path))

    for generated_prefix in generated_prefixes:
        app.add_api_route(
            f"{generated_prefix}/{{asset_path:path}}",
            generated_asset,
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )

    app.include_router(prompts_router, prefix="/api")
    app.include_router(generation_router, prefix="/api")
    app.include_router(audit_router, prefix="/api")
    app.include_router(album_router, prefix="/api")
    app.include_router(references_router, prefix="/api")
    app.include_router(agent_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")
    app.include_router(blender_router, prefix="/api")

    return app

def _generated_public_prefixes(public_prefix: str) -> list[str]:
    return [f"/{public_prefix.strip('/')}"]


def _csv_values(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


app = create_app()

