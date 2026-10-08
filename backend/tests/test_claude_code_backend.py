import asyncio
import json
from pathlib import Path

import pytest

from app.generation.backends import claude_code
from app.generation.backends.types import ReferenceRejectedError
from app.generation.detail_analysis import DetailAnalysisProviderRequest

REFERENCE_KEY = "front:references/upload-1/front.webp"
DETAIL_RESULT = {
    "features": [{"id": "feature-hair", "kind": "hair", "label": "Hair", "description": "Long light blue hair"}],
    "crops": [
        {
            "id": "crop-hair",
            "kind": "hair",
            "description": "Hair shape",
            "source_reference_key": REFERENCE_KEY,
            "bbox": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5},
        }
    ],
    "warnings": [],
}


def cli_output(result: str, *, is_error: bool = False, **extra) -> bytes:
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": is_error,
        "result": result,
        "usage": {
            "input_tokens": 10,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 5,
            "output_tokens": 20,
        },
        "permission_denials": [],
        **extra,
    }
    return json.dumps(payload).encode("utf-8")


class FakeProcess:
    def __init__(self, stdout: bytes, returncode: int = 0, delay: float = 0.0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.delay = delay
        self.stdin_data: bytes | None = None
        self.killed = False

    async def communicate(self, input=None):
        self.stdin_data = input
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.stdout, b""

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


@pytest.fixture
def analysis_env(tmp_path: Path, monkeypatch):
    reference_root = tmp_path / "references"
    reference_file = reference_root / "upload-1" / "front.webp"
    reference_file.parent.mkdir(parents=True)
    reference_file.write_bytes(b"fake-image")
    settings = type(
        "Settings",
        (),
        {
            "claude_code_path": str(tmp_path / "bin" / "claude"),
            "claude_code_model": "sonnet",
            "claude_code_workspace_dir": str(tmp_path / "claude-code"),
            "claude_code_timeout_seconds": 30,
            "reference_upload_dir": str(reference_root),
        },
    )()
    monkeypatch.setattr(claude_code, "get_settings", lambda: settings)
    return settings, tmp_path / "claude-code" / "session-a" / "detail-analysis-analysis-a"


def make_request() -> DetailAnalysisProviderRequest:
    return DetailAnalysisProviderRequest(
        analysis_id="analysis-a",
        character_session_id="session-a",
        free_text="keep the X clip",
        reference_keys=[REFERENCE_KEY],
    )


async def test_claude_code_runs_safety_then_detail_analysis(analysis_env, monkeypatch):
    _settings, workspace = analysis_env
    processes: list[FakeProcess] = []
    calls: list[tuple[list[str], dict]] = []

    async def fake_exec(*args, **kwargs):
        calls.append(([str(item) for item in args], kwargs))
        if not processes:
            process = FakeProcess(cli_output('```json\n{"allowed": true, "reason": "ok", "message": ""}\n```'))
        else:
            process = FakeProcess(cli_output(json.dumps(DETAIL_RESULT)))
        processes.append(process)
        return process

    monkeypatch.setattr(claude_code.asyncio, "create_subprocess_exec", fake_exec)

    result = await claude_code.ClaudeCodeLLMBackend().analyze_reference_details(make_request())

    assert result.features[0].description == "Long light blue hair"
    assert result.crops[0].source_reference_key == REFERENCE_KEY
    assert (workspace / "refs" / "ref-1.webp").read_bytes() == b"fake-image"
    assert len(calls) == 2
    for (args, kwargs), process, prompt_name in zip(
        calls, processes, ["reference-safety-prompt.md", "detail-analysis-prompt.md"]
    ):
        assert args[:3] == [str(analysis_env[0].claude_code_path), "-p", "--output-format"]
        assert args[args.index("--tools") + 1] == "Read"
        assert args[args.index("--allowedTools") + 1] == "Read"
        assert args[args.index("--permission-mode") + 1] == "dontAsk"
        assert args[args.index("--model") + 1] == "sonnet"
        assert kwargs["cwd"] == str(workspace)
        prompt = (workspace / prompt_name).read_text(encoding="utf-8")
        assert process.stdin_data == prompt.encode("utf-8")
        assert "refs/ref-1.webp" in prompt


async def test_claude_code_rejection_raises_reference_rejected(analysis_env, monkeypatch):
    async def fake_exec(*args, **kwargs):
        return FakeProcess(cli_output('{"allowed": false, "reason": "adult_explicit", "message": "no"}'))

    monkeypatch.setattr(claude_code.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ReferenceRejectedError) as exc_info:
        await claude_code.ClaudeCodeLLMBackend().analyze_reference_details(make_request())

    assert exc_info.value.reason == "reference_adult_explicit"


async def test_claude_code_missing_reference_fails_before_cli(analysis_env, monkeypatch):
    async def fake_exec(*args, **kwargs):
        raise AssertionError("CLI must not start")

    monkeypatch.setattr(claude_code.asyncio, "create_subprocess_exec", fake_exec)
    request = make_request()
    request.reference_keys = ["front:references/upload-404/front.webp"]

    with pytest.raises(RuntimeError, match="reference image not found"):
        await claude_code.ClaudeCodeLLMBackend().analyze_reference_details(request)


async def test_claude_code_timeout_kills_process(analysis_env, monkeypatch):
    settings, _workspace = analysis_env
    settings.claude_code_timeout_seconds = 0.05
    process = FakeProcess(cli_output("{}"), delay=1)

    async def fake_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(claude_code.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(RuntimeError, match="timed out"):
        await claude_code.ClaudeCodeLLMBackend().analyze_reference_details(make_request())
    assert process.killed is True


def test_parse_output_reports_login_error():
    stdout = cli_output("Not logged in · Please run /login", is_error=True, api_error_status=401)

    with pytest.raises(RuntimeError, match="未登录"):
        claude_code.parse_claude_code_output(stdout, b"", 1)


def test_parse_output_rejects_non_json_stdout():
    with pytest.raises(RuntimeError, match="exit code 2"):
        claude_code.parse_claude_code_output(b"boom", b"segfault", 2)


def test_parse_output_maps_token_usage_and_strips_fence():
    result = claude_code.parse_claude_code_output(cli_output('```json\n{"a": 1}\n```'), b"", 0)

    assert result.text == '{"a": 1}'
    assert result.token_usage is not None
    assert result.token_usage.input_tokens == 115
    assert result.token_usage.cached_input_tokens == 100
    assert result.token_usage.output_tokens == 20
    assert result.token_usage.total_tokens == 135


async def test_claude_code_cannot_generate_images():
    with pytest.raises(RuntimeError, match="cannot generate images"):
        await claude_code.ClaudeCodeLLMBackend().generate("job", {})


def test_command_omits_model_when_not_configured():
    settings = type("Settings", (), {"claude_code_path": "/opt/claude", "claude_code_model": ""})()

    command = claude_code.build_claude_code_command(settings)

    assert "--model" not in command


def test_environment_drops_host_session_variables_when_nested():
    base = {
        "CLAUDECODE": "1",
        "CLAUDE_CODE_SIMPLE": "1",
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:9999",
        "ANTHROPIC_AUTH_TOKEN": "host-token",
        "PATH": "/usr/bin",
        "HOME": "/root",
    }

    environment = claude_code.claude_code_environment(base)

    assert environment == {"PATH": "/usr/bin", "HOME": "/root"}
    assert base["CLAUDECODE"] == "1"


def test_environment_keeps_deployment_anthropic_settings():
    base = {"ANTHROPIC_BASE_URL": "https://proxy.example", "ANTHROPIC_AUTH_TOKEN": "deploy", "PATH": "/usr/bin"}

    assert claude_code.claude_code_environment(base) == base


async def test_cli_receives_cleaned_environment(analysis_env, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    environments = []

    async def fake_exec(*args, **kwargs):
        environments.append(kwargs["env"])
        return FakeProcess(cli_output('{"allowed": false, "reason": "unusable_reference", "message": "x"}'))

    monkeypatch.setattr(claude_code.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ReferenceRejectedError):
        await claude_code.ClaudeCodeLLMBackend().analyze_reference_details(make_request())
    assert "CLAUDECODE" not in environments[0]
    assert "CLAUDE_CODE_SIMPLE" not in environments[0]
