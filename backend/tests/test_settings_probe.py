import json

import httpx
import pytest

from app.core import chat_api
from app.core.config import get_settings

GLM_REJECTION = {"error": {"code": "InvalidParameter", "message": "thinking.type `disabled` is not supported"}}


@pytest.fixture(autouse=True)
def endpoint(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("AGENT_LLM_API_KEY", "key")
    monkeypatch.setenv("AGENT_LLM_MODEL", "glm-5")
    monkeypatch.setenv("AGENT_LLM_EXTRA_BODY", '{"thinking": {"type": "disabled"}}')
    get_settings.cache_clear()
    chat_api.forget_rejected_extra_body()
    yield
    chat_api.forget_rejected_extra_body()


def install(monkeypatch, handler) -> list[dict]:
    bodies: list[dict] = []

    def recording(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        return handler(body)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        chat_api.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(recording), **kwargs)
    )
    return bodies


def reply(message: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": message}]})


TOOL_CALL = {"content": "", "tool_calls": [{"id": "c1", "function": {"name": "ping", "arguments": '{"ok": true}'}}]}


async def test_agent_probe_reports_dropped_extra_fields_and_tool_support(async_client, monkeypatch):
    install(monkeypatch, lambda body: httpx.Response(400, json=GLM_REJECTION) if "thinking" in body else reply(TOOL_CALL))

    response = await async_client.post("/api/settings/probe/agent")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["model"] == "glm-5"
    assert {check["id"]: check["status"] for check in body["checks"]} == {
        "connection": "ok",
        "extra_body": "warn",
        "tools": "ok",
    }


async def test_agent_probe_flags_missing_tool_calls(async_client, monkeypatch):
    install(monkeypatch, lambda body: reply({"content": "pong"}))

    body = (await async_client.post("/api/settings/probe/agent")).json()

    assert body["ok"] is False
    assert body["checks"][-1] == {"id": "tools", "status": "fail", "detail": "pong"}


async def test_agent_probe_classifies_auth_errors(async_client, monkeypatch):
    install(monkeypatch, lambda body: httpx.Response(401, json={"error": "invalid key"}))

    body = (await async_client.post("/api/settings/probe/agent")).json()

    assert body["ok"] is False
    assert body["checks"][0]["status"] == "fail"
    assert body["checks"][0]["detail"].startswith("auth: HTTP 401")


async def test_analysis_probe_sends_an_image(async_client, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("AGENT_LLM_EXTRA_BODY", "")
    get_settings.cache_clear()
    bodies = install(monkeypatch, lambda body: reply({"content": "Red."}))

    body = (await async_client.post("/api/settings/probe/analysis")).json()

    assert body["ok"] is True
    assert body["checks"] == [{"id": "connection", "status": "ok", "detail": ""}, {"id": "vision", "status": "ok", "detail": "Red."}]
    image = bodies[0]["messages"][0]["content"][1]["image_url"]["url"]
    assert image.startswith("data:image/png;base64,")


async def test_probe_rejects_roles_that_are_not_openai_compatible(async_client):
    # The test app uses fixture analysis.
    assert (await async_client.post("/api/settings/probe/analysis")).status_code == 400
    assert (await async_client.post("/api/settings/probe/other")).status_code == 400


async def test_extra_body_setting_must_be_a_json_object(async_client):
    assert (await async_client.put("/api/settings", json={"values": {"agent_llm_extra_body": "[1]"}})).status_code == 400
    response = await async_client.put("/api/settings", json={"values": {"agent_llm_extra_body": ""}})
    assert response.status_code == 200
    assert response.json()["values"]["agent_llm_extra_body"] == ""
