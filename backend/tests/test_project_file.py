"""Version PNG download and .vkp project-file export/import."""

import io
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
