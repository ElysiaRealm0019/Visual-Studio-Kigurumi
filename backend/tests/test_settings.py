import json

from app.core.config import get_settings


async def test_settings_report_effective_backends(async_client):
    response = await async_client.get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["writable"] is True
    assert body["values"]["llm_provider"] == "fixture"
    assert body["values"]["image_provider"] == "fixture"
    assert body["overridden"] == []
    assert "ark" in body["options"]["image_provider"]
    assert "ark_api_key" not in body["values"]
    assert body["secrets"]["ark_api_key"] == {"set": False, "source": "", "hint": ""}


async def test_settings_update_overrides_env_and_resets(async_client, tmp_path):
    response = await async_client.put(
        "/api/settings",
        json={"values": {"image_provider": "ark", "agent_llm_provider": "claude_code", "agent_llm_model": " m1 "}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["values"]["image_provider"] == "ark"
    assert body["values"]["agent_llm_model"] == "m1"
    assert set(body["overridden"]) == {"image_provider", "agent_llm_provider", "agent_llm_model"}
    assert get_settings().image_provider == "ark"
    saved = json.loads((tmp_path / "settings-overrides.json").read_text(encoding="utf-8"))
    assert saved["agent_llm_provider"] == "claude_code"

    response = await async_client.put("/api/settings", json={"values": {"image_provider": None}})
    assert response.json()["values"]["image_provider"] == "fixture"
    assert get_settings().agent_llm_provider == "claude_code"

    response = await async_client.delete("/api/settings")
    assert response.json()["overridden"] == []
    assert get_settings().agent_llm_provider == "openai_compatible"


async def test_settings_reject_unknown_and_invalid_values(async_client):
    for values in (
        {"image_provider": "dalle"},
        {"jwt_secret": "secret"},
        {"agent_llm_model": "x" * 301},
        {"ark_api_key": "  "},
        {"ark_api_key": "two words"},
    ):
        response = await async_client.put("/api/settings", json={"values": values})
        assert response.status_code == 400
    assert (await async_client.get("/api/settings")).json()["overridden"] == []


async def test_settings_are_read_only_in_production(async_client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()

    response = await async_client.put("/api/settings", json={"values": {"image_provider": "ark"}})

    assert response.status_code == 403
    assert "fixture" not in (await async_client.get("/api/settings")).json()["options"]["image_provider"]


def test_unreadable_override_file_is_ignored(monkeypatch, tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("RUNTIME_SETTINGS_PATH", str(path))
    get_settings.cache_clear()

    assert get_settings().image_provider == ""


async def test_api_keys_are_write_only(async_client, monkeypatch):
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-from-env-0000")
    get_settings.cache_clear()

    response = await async_client.put("/api/settings", json={"values": {"ark_api_key": " ark-secret-key-1234 "}})

    assert response.status_code == 200
    assert "ark-secret-key-1234" not in response.text
    secrets = response.json()["secrets"]
    assert secrets["ark_api_key"] == {"set": True, "source": "override", "hint": "…1234"}
    assert secrets["siliconflow_api_key"] == {"set": True, "source": "env", "hint": "…0000"}
    assert response.json()["status"]["ark_key"] is True
    assert get_settings().ark_api_key == "ark-secret-key-1234"
    # The agent reuses the Ark key when it talks to the same endpoint.
    assert response.json()["status"]["agent_llm_key"] is True

    response = await async_client.put("/api/settings", json={"values": {"ark_api_key": None}})
    assert response.json()["secrets"]["ark_api_key"]["set"] is False
