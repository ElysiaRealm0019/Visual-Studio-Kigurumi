import json
from pathlib import Path

import pytest
from PIL import Image

from app.generation.backends import codex

THREAD_ID = "01a1185a-46a0-7793-93fa-517a4c5731f8"


def events(*, with_command: bool) -> bytes:
    lines = [{"type": "thread.started", "thread_id": THREAD_ID}]
    if with_command:
        lines.append(
            {
                "type": "item.completed",
                "item": {"type": "command_execution", "command": "Get-Content SKILL.md", "exit_code": 0},
            }
        )
    lines.append({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
    return "\n".join(json.dumps(line) for line in lines).encode("utf-8")


@pytest.fixture
def setup(tmp_path: Path, monkeypatch):
    reference_root = tmp_path / "references"
    (reference_root / "upload-1").mkdir(parents=True)
    Image.new("RGB", (600, 800), "blue").save(reference_root / "upload-1" / "front.png")
    settings = type(
        "Settings",
        (),
        {
            "codex_path": "codex",
            "codex_workspace_dir": str(tmp_path / "workspace"),
            "codex_output_dir": str(tmp_path / "generated"),
            "generated_public_prefix": "/api/generated",
            "codex_product_reference_path": str(tmp_path / "missing-product.png"),
            "reference_upload_dir": str(reference_root),
        },
    )()
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(codex, "get_settings", lambda: settings)
    monkeypatch.setattr(codex, "apply_kigcraft_watermark", lambda path: None)
    return tmp_path, codex_home


def fake_exec_factory(stdout: bytes, on_run=None):
    class FakeProcess:
        returncode = 0
        pid = 1

        async def communicate(self):
            if on_run:
                on_run()
            return stdout, b""

    async def fake_exec(*args, **kwargs):
        return FakeProcess()

    return fake_exec


PAYLOAD = {
    "character_session_id": "session-a",
    "generation_mode": "turnaround",
    "reference_keys": ["front:references/upload-1/front.png"],
}


async def test_tool_generated_image_is_used_and_helper_commands_are_allowed(setup, monkeypatch):
    tmp_path, codex_home = setup

    def write_tool_image():
        directory = codex_home / "generated_images" / THREAD_ID
        directory.mkdir(parents=True)
        Image.new("RGB", (1536, 1024), "red").save(directory / "exec-1.png")

    monkeypatch.setattr(
        codex.asyncio, "create_subprocess_exec", fake_exec_factory(events(with_command=True), write_tool_image)
    )

    outputs = await codex.CodexImageProvider().generate("job-1", PAYLOAD)

    assert [(o.index, o.width, o.height) for o in outputs] == [(1, 3000, 2000)]
    assert outputs[0].image_url == "/api/generated/session-a/job-1/outputs/candidate-1.webp"
    assert outputs[0].object_key == "codex/job-1/outputs/candidate-1.webp"
    public_file = tmp_path / "generated" / "session-a" / "job-1" / "outputs" / "candidate-1.webp"
    with Image.open(public_file) as saved:
        assert saved.size == (3000, 2000)
        assert saved.convert("RGB").getpixel((1500, 1000))[0] > 200


async def test_without_tool_images_commands_are_still_rejected(setup, monkeypatch):
    monkeypatch.setattr(codex.asyncio, "create_subprocess_exec", fake_exec_factory(events(with_command=True)))

    with pytest.raises(RuntimeError, match="command execution instead of the image generation tool"):
        await codex.CodexImageProvider().generate("job-2", PAYLOAD)


def test_thread_id_with_path_characters_is_ignored(setup):
    stdout = json.dumps({"type": "thread.started", "thread_id": "../../etc"}).encode()

    assert codex._codex_tool_generated_images(stdout) == []


def test_collection_note_only_in_direct_prompts():
    payload = {**PAYLOAD, "user_requirements": [], "user_notes": ""}

    assert codex.TOOL_OUTPUT_COLLECTION_NOTE in codex._build_codex_prompt(payload)
    assert codex.TOOL_OUTPUT_COLLECTION_NOTE not in codex._build_codex_candidate_prompt(payload, 1)
