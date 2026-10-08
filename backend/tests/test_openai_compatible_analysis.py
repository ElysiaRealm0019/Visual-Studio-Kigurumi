import base64
import json
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.core import chat_api
from app.core.config import get_settings
from app.generation.backends import openai_compatible
from app.generation.backends.types import ReferenceRejectedError
from app.generation.detail_analysis import DetailAnalysisProviderRequest
from app.generation.provider import CompositeProvider, get_generation_provider

REFERENCE_KEY = "front:references/upload-1/front.png"
DETAIL_RESULT = {
    "features": [{"id": "feature-hair", "kind": "hair", "label": "Hair", "description": "Long light blue hair"}],
    "crops": [],
    "warnings": [],
}


@pytest.fixture
def analysis_env(tmp_path: Path, monkeypatch):
    reference_file = tmp_path / "references" / "upload-1" / "front.png"
    reference_file.parent.mkdir(parents=True)
    buffer = BytesIO()
    Image.new("RGB", (3000, 1500), (200, 100, 50)).save(buffer, format="PNG")
    reference_file.write_bytes(buffer.getvalue())
    monkeypatch.setenv("REFERENCE_UPLOAD_DIR", str(tmp_path / "references"))
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://agent.test/v1/")
    monkeypatch.setenv("AGENT_LLM_API_KEY", "agent-key")
    monkeypatch.setenv("AGENT_LLM_MODEL", "agent-model")
    monkeypatch.setenv("AGENT_LLM_EXTRA_BODY", '{"thinking": {"type": "disabled"}}')
    monkeypatch.setenv("ANALYSIS_LLM_MAX_IMAGE_SIDE", "512")
    get_settings.cache_clear()


def install_responses(monkeypatch, replies: list[str]) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": replies[len(requests) - 1]}}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        chat_api.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return requests


def make_request() -> DetailAnalysisProviderRequest:
    return DetailAnalysisProviderRequest(
        analysis_id="analysis-a", character_session_id="session-a", free_text="", reference_keys=[REFERENCE_KEY]
    )


async def test_runs_safety_then_analysis_with_images(analysis_env, monkeypatch):
    requests = install_responses(
        monkeypatch,
        ['Sure:\n```json\n{"allowed": true, "reason": "ok", "message": ""}\n```', json.dumps(DETAIL_RESULT)],
    )

    result = await openai_compatible.OpenAICompatibleLLMBackend().analyze_reference_details(make_request())

    assert result.features[0].description == "Long light blue hair"
    assert len(requests) == 2
    for request in requests:
        assert str(request.url) == "https://agent.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer agent-key"
        body = json.loads(request.content)
        assert body["model"] == "agent-model"
        assert body["thinking"] == {"type": "disabled"}
        content = body["messages"][0]["content"]
        assert content[0]["type"] == "text"
        image_url = content[1]["image_url"]["url"]
        assert image_url.startswith("data:image/png;base64,")
        assert len(content) == 2
    # The 3000px reference is downscaled before upload.
    encoded = json.loads(requests[0].content)["messages"][0]["content"][1]["image_url"]["url"].split(",", 1)[1]
    assert max(Image.open(BytesIO(base64.b64decode(encoded))).size) == 512


async def test_rejected_reference_stops_before_analysis(analysis_env, monkeypatch):
    requests = install_responses(
        monkeypatch, ['{"allowed": false, "reason": "adult_explicit", "message": "not allowed"}']
    )

    with pytest.raises(ReferenceRejectedError) as error:
        await openai_compatible.OpenAICompatibleLLMBackend().analyze_reference_details(make_request())

    assert error.value.reason == "reference_adult_explicit"
    assert len(requests) == 1


async def test_http_error_is_reported(analysis_env, monkeypatch):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        chat_api.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(400, text="model does not support images")),
            **kwargs,
        ),
    )

    with pytest.raises(RuntimeError, match="HTTP 400: model does not support images"):
        await openai_compatible.OpenAICompatibleLLMBackend().analyze_reference_details(make_request())


def test_endpoint_prefers_analysis_settings_and_reuses_keys(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://agent.test/v1")
    monkeypatch.setenv("AGENT_LLM_API_KEY", "agent-key")
    monkeypatch.setenv("AGENT_LLM_MODEL", "agent-model")
    monkeypatch.setenv("ARK_BASE_URL", "https://ark.test/v3")
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    get_settings.cache_clear()
    assert openai_compatible.resolve_analysis_endpoint(get_settings()) == (
        "https://agent.test/v1",
        "agent-key",
        "agent-model",
    )

    monkeypatch.setenv("ANALYSIS_LLM_BASE_URL", "https://ark.test/v3/")
    monkeypatch.setenv("ANALYSIS_LLM_MODEL", "vision-model")
    get_settings.cache_clear()
    assert openai_compatible.resolve_analysis_endpoint(get_settings()) == (
        "https://ark.test/v3",
        "ark-key",
        "vision-model",
    )

    monkeypatch.setenv("ANALYSIS_LLM_API_KEY", "analysis-key")
    get_settings.cache_clear()
    assert openai_compatible.resolve_analysis_endpoint(get_settings())[1] == "analysis-key"


def test_factory_builds_openai_compatible_analysis(monkeypatch):
    monkeypatch.setenv("GENERATION_PROVIDER", "codex")
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("IMAGE_PROVIDER", "ark")
    get_settings.cache_clear()

    provider = get_generation_provider()

    assert isinstance(provider, CompositeProvider)
    assert isinstance(provider.llm, openai_compatible.OpenAICompatibleLLMBackend)
    assert provider.name == "openai_compatible+ark"
