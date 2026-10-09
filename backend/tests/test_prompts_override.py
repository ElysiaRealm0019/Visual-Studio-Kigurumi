"""Hot-reloadable prompt overrides (PROMPTS_FILE, default runtime/prompts.json)."""

import json
from pathlib import Path

import pytest

from app.agent import runner as runner_module
from app.agent.store import conversation_store
from app.core.config import get_settings
from app.generation import job_store
from app.generation.backends import codex, prompting
from app.generation.modes import AI_OUTPUT_LANDMARKS_ENABLED
from app.prompts import overrides
from tests.test_product_reference_prompts import PAYLOAD

DEFAULT_LOOK_SNIPPET = "photorealistic studio product photograph"


@pytest.fixture
def prompts_env(tmp_path: Path, monkeypatch):
    path = tmp_path / "prompts.json"
    monkeypatch.setenv("PROMPTS_FILE", str(path))
    get_settings.cache_clear()
    overrides.reset_prompt_overrides_cache()
    yield path
    overrides.reset_prompt_overrides_cache()
    get_settings.cache_clear()


def write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def front_prompt() -> str:
    return codex._build_codex_prompt({**PAYLOAD, "generation_mode": "front_design"})


def test_override_changes_assembled_prompt(prompts_env):
    write(prompts_env, {"head_shell_look": "Look: TEST LOOK OVERRIDE."})

    prompt = front_prompt()

    assert "Look: TEST LOOK OVERRIDE." in prompt
    assert DEFAULT_LOOK_SNIPPET not in prompt


def test_file_change_applies_without_restart(prompts_env):
    write(prompts_env, {"head_shell_look": "Look: FIRST VERSION."})
    assert "FIRST VERSION." in front_prompt()

    write(prompts_env, {"head_shell_look": "Look: SECOND VERSION is longer."})
    prompt = front_prompt()

    assert "SECOND VERSION is longer." in prompt
    assert "FIRST VERSION." not in prompt


def test_empty_or_invalid_values_fall_back_to_defaults(prompts_env):
    write(prompts_env, {"head_shell_look": "   "})
    assert DEFAULT_LOOK_SNIPPET in front_prompt()

    write(prompts_env, {"head_shell_look": 42, "constraints.front_design": "not-a-list"})
    assert DEFAULT_LOOK_SNIPPET in front_prompt()
    assert job_store._system_constraints_for_mode("front_design", []) == job_store._FRONT_DESIGN_CONSTRAINTS

    prompts_env.write_text("{not json", encoding="utf-8")
    assert DEFAULT_LOOK_SNIPPET in front_prompt()


def test_missing_file_uses_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PROMPTS_FILE", str(tmp_path / "absent.json"))
    get_settings.cache_clear()
    overrides.reset_prompt_overrides_cache()
    assert DEFAULT_LOOK_SNIPPET in front_prompt()


def test_style_photo_ignore_propagates_into_style_lines(prompts_env):
    write(prompts_env, {"style_photo_ignore": "TEST IGNORE RULE."})

    prompt = codex._build_codex_prompt({**PAYLOAD, "generation_mode": "front_design"})
    assert "TEST IGNORE RULE." in prompt


def test_constraint_list_override_keeps_landmark_splice(prompts_env):
    write(prompts_env, {"constraints.front_design": ["Custom constraint one.", "Custom constraint two."]})

    constraints = job_store._system_constraints_for_mode("front_design", [])

    assert constraints[0] == "Custom constraint one."
    joined = " ".join(constraints)
    assert ("Return pure JSON landmarks" in joined) is AI_OUTPUT_LANDMARKS_ENABLED
    assert "User text may describe preferences" not in joined


def test_agent_system_prompt_override_and_bad_template_fallback(prompts_env):
    conversation = conversation_store.create("zh-CN")
    write(prompts_env, {"agent.system_prompt": "You are a TEST ASSISTANT speaking {language} with state {state}."})
    prompt = runner_module.build_system_prompt(conversation)
    assert prompt.startswith("You are a TEST ASSISTANT speaking Simplified Chinese")

    write(prompts_env, {"agent.system_prompt": "Broken {nonsense_placeholder} template"})
    prompt = runner_module.build_system_prompt(conversation)
    assert "V.S.K" in prompt and "TEST ASSISTANT" not in prompt


def test_stage_lists_stay_lazy_through_the_registry_constants(prompts_env):
    # The default constants remain importable for the settings inventory endpoint.
    assert "photorealistic studio product photograph" in prompting.HEAD_SHELL_LOOK
    assert callable(prompting._final_front_view_prompt)
    assert callable(prompting.CHARACTER_STAGE_PROMPTS["character_front"])


async def test_settings_prompts_inventory_endpoint(prompts_env, async_client):
    response = await async_client.get("/api/settings/prompts")

    assert response.status_code == 200
    body = response.json()
    assert body["head_shell_look"]["overridden"] is False
    assert body["head_shell_look"]["default"] == prompting.HEAD_SHELL_LOOK
    assert body["agent.system_prompt"]["value"] == runner_module.SYSTEM_PROMPT
    assert body["constraints.front_design"]["kind"] == "list"

    write(prompts_env, {"head_shell_look": "Look: OVERRIDDEN FOR INVENTORY."})
    body = (await async_client.get("/api/settings/prompts")).json()
    assert body["head_shell_look"]["overridden"] is True
    assert body["head_shell_look"]["value"] == "Look: OVERRIDDEN FOR INVENTORY."
    assert body["head_shell_look"]["default"] == prompting.HEAD_SHELL_LOOK
