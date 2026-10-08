import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app.agent import runner as runner_module
from app.agent import tools as tools_module
from app.agent.llm import (
    AssistantTurn,
    ToolCall,
    parse_claude_code_reply,
    parse_openai_message,
    render_claude_code_prompt,
)
from app.agent.runner import agent_runner
from app.agent.store import conversation_store


class FakeLLM:
    """Plays back a policy function: (state, llm_messages) -> AssistantTurn."""

    name = "fake"

    def __init__(self, policy):
        self.policy = policy
        self.calls: list[str] = []

    async def complete(self, system_prompt, messages, tools, *, workspace, readable_files):
        self.calls.append(system_prompt)
        return self.policy(messages)


def call(name: str, **arguments) -> ToolCall:
    return ToolCall(id=f"call-{name}-{len(arguments)}", name=name, arguments=arguments)


def last_tool_results(messages) -> list[dict]:
    results = []
    for message in reversed(messages):
        if message["role"] != "tool":
            break
        results.append(json.loads(message["content"]))
    return list(reversed(results))


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 80), "pink").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def agent_env(test_app, monkeypatch, tmp_path: Path):
    from app.core.config import get_settings

    monkeypatch.setenv("AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("CLEAN_OUTPUT_DIR", str(tmp_path / "clean"))
    get_settings.cache_clear()
    conversation_store.clear()
    monkeypatch.setattr(tools_module, "JOB_POLL_SECONDS", 0.01)
    return test_app


def use_llm(monkeypatch, policy) -> FakeLLM:
    llm = FakeLLM(policy)
    monkeypatch.setattr(runner_module, "get_agent_llm", lambda: llm)
    return llm


async def start_conversation(client) -> str:
    response = await client.post("/agent/conversations", json={"locale": "zh-CN"})
    assert response.status_code == 200
    return response.json()["id"]


async def send(client, conversation_id: str, *, text: str = "", files: int = 0, **form):
    response = await client.post(
        f"/agent/conversations/{conversation_id}/messages",
        data={"text": text, **form},
        files=[("files", (f"ref{i}.png", png_bytes(), "image/png")) for i in range(files)] or None,
    )
    assert response.status_code == 200, response.text
    await agent_runner.wait(conversation_id)
    return (await client.get(f"/agent/conversations/{conversation_id}")).json()


def auto_front_policy(messages):
    """analyze -> head shell front (design stage skipped) -> ask the user."""
    tool_names = [m.get("name") for m in messages if m["role"] == "tool"]
    if "analyze_references" not in tool_names:
        return AssistantTurn(text="我先分析一下参考图。", tool_calls=[call("analyze_references")])
    if "generate_front_view" not in tool_names:
        return AssistantTurn(
            text="", tool_calls=[call("generate_front_view", instructions="soft eyes", skip_design=True)]
        )
    return AssistantTurn(text="正视图好了，看看可以吗？")


def auto_design_policy(messages):
    """analyze -> character design -> ask the user."""
    tool_names = [m.get("name") for m in messages if m["role"] == "tool"]
    if "analyze_references" not in tool_names:
        return AssistantTurn(text="我先分析一下参考图。", tool_calls=[call("analyze_references")])
    if "generate_design" not in tool_names:
        return AssistantTurn(text="", tool_calls=[call("generate_design", instructions="soft eyes")])
    return AssistantTurn(text="设定图好了，看看可以吗？")


def job_for(body, image_id):
    from app.generation.job_store import job_store

    image = next(image for image in body["state"]["images"] if image["id"] == image_id)
    return job_store.get(image["job_id"])


async def test_upload_runs_analysis_and_design_then_waits(agent_env, async_client, monkeypatch):
    use_llm(monkeypatch, auto_design_policy)
    conversation_id = await start_conversation(async_client)

    body = await send(async_client, conversation_id, text="帮我做这个角色", files=2)

    assert body["status"] == "idle"
    types = [event["type"] for event in body["events"]]
    assert types[:2] == ["user_message", "run_state"]
    tools = [event for event in body["events"] if event["type"] == "tool"]
    assert [(tool["tool"], tool["status"]) for tool in tools] == [
        ("analyze_references", "succeeded"),
        ("generate_design", "succeeded"),
    ]
    images = [event for event in body["events"] if event["type"] == "image"]
    assert len(images) == 1 and images[0]["role"] == "design"
    assert body["events"][-2]["type"] == "assistant_message"
    references = body["state"]["references"]
    assert [reference["kind"] for reference in references] == ["front", "supplemental"]
    assert body["state"]["current_design_id"] == "design-1"
    assert body["state"]["approved_design_id"] is None
    assert body["state"]["current_front_id"] is None
    job = job_for(body, "design-1")
    assert job.generation_mode == "character_front"
    assert job.prompt_payload["reference_keys"][0].startswith("front:")
    # The generated design is copied into the reference area so later jobs can attach it.
    reference_key = body["state"]["images"][0]["reference_key"]
    from app.core.config import get_settings

    assert (Path(get_settings().reference_upload_dir) / reference_key.removeprefix("references/")).is_file()


async def test_head_shell_requires_an_approved_design(agent_env, async_client, monkeypatch):
    def policy(messages):
        tool_names = [m.get("name") for m in messages if m["role"] == "tool"]
        if not tool_names:
            return AssistantTurn(text="", tool_calls=[call("generate_design")])
        if "approve_design" not in tool_names:
            # Tries to approve its own design in the same run: must be refused.
            return AssistantTurn(text="", tool_calls=[call("approve_design", image_id="design-1")])
        if "generate_front_view" not in tool_names:
            return AssistantTurn(text="", tool_calls=[call("generate_front_view")])
        return AssistantTurn(text="等你确认。")

    use_llm(monkeypatch, policy)
    conversation_id = await start_conversation(async_client)
    body = await send(async_client, conversation_id, files=1)

    tools = {event["tool"]: event for event in body["events"] if event["type"] == "tool"}
    assert tools["approve_design"]["status"] == "failed"
    assert tools["generate_front_view"]["status"] == "failed"
    assert "approved character design" in tools["generate_front_view"]["error"]
    assert [image["role"] for image in body["state"]["images"]] == ["design"]


async def test_approved_design_drives_head_shell_and_turnaround(agent_env, async_client, monkeypatch):
    def four_view_design(messages):
        if messages[-1]["role"] == "user":
            return AssistantTurn(text="", tool_calls=[call("generate_design", view="turnaround")])
        return AssistantTurn(text="四视图设定好了。")

    use_llm(monkeypatch, four_view_design)
    conversation_id = await start_conversation(async_client)
    body = await send(async_client, conversation_id, files=1)
    assert job_for(body, "design_turnaround-1").generation_mode == "character_turnaround"

    def head_shell(messages):
        if messages[-1]["role"] == "user":
            return AssistantTurn(text="开始做头壳。", tool_calls=[call("generate_front_view")])
        return AssistantTurn(text="头壳正视图好了。")

    use_llm(monkeypatch, head_shell)
    body = await send(async_client, conversation_id, action="approve_design", image_id="design_turnaround-1")

    assert body["state"]["approved_design_id"] == "design_turnaround-1"
    front = body["state"]["images"][-1]
    assert front["role"] == "front" and front["parent_id"] == "design_turnaround-1"
    job = job_for(body, front["id"])
    assert job.generation_mode == "front_design"
    # Only the approved design is attached: raw references would undo the user's design edits.
    assert len(job.prompt_payload["reference_keys"]) == 1
    assert job.prompt_payload["reference_keys"][0].startswith("design:references/agent/")
    assert "approved 2D character design" in job.prompt_payload["reference_descriptions"][0]["description"]
    user_event = next(event for event in reversed(body["events"]) if event["type"] == "user_message")
    assert user_event["action"] == "approve_design"
    assert any(event["type"] == "design_approved" for event in body["events"])

    def turnaround(messages):
        if messages[-1]["role"] == "user":
            return AssistantTurn(text="", tool_calls=[call("generate_turnaround")])
        return AssistantTurn(text="完成。")

    use_llm(monkeypatch, turnaround)
    body = await send(async_client, conversation_id, action="approve_front", image_id=front["id"])
    keys = job_for(body, "turnaround-1").prompt_payload["reference_keys"]
    assert keys[0].startswith("front:") and keys[1].startswith("design_extra:")


async def test_revise_design_and_expand_front_design_to_four_views(agent_env, async_client, monkeypatch):
    use_llm(monkeypatch, auto_design_policy)
    conversation_id = await start_conversation(async_client)
    await send(async_client, conversation_id, files=1)

    def revise_then_expand(messages):
        tool_names = [m.get("name") for m in messages if m["role"] == "tool"]
        if "revise_design" not in tool_names:
            return AssistantTurn(text="", tool_calls=[call("revise_design", instructions="shorter bangs")])
        if tool_names.count("generate_design") < 2:
            return AssistantTurn(
                text="", tool_calls=[call("generate_design", view="turnaround", base_image_id="design-2")]
            )
        return AssistantTurn(text="好了。")

    use_llm(monkeypatch, revise_then_expand)
    body = await send(async_client, conversation_id, text="刘海短一点，然后出四视图")

    revised = job_for(body, "design-2")
    assert revised.generation_mode == "character_revision"
    expanded = job_for(body, "design_turnaround-1")
    assert expanded.prompt_payload["reference_keys"][0].startswith("design:")
    assert all(key.startswith("supplemental:") for key in expanded.prompt_payload["reference_keys"][1:])
    assert body["state"]["current_design_id"] == "design_turnaround-1"


async def test_turnaround_requires_user_approval(agent_env, async_client, monkeypatch):
    def policy(messages):
        tool_names = [m.get("name") for m in messages if m["role"] == "tool"]
        if not tool_names:
            return AssistantTurn(text="", tool_calls=[call("generate_front_view", skip_design=True)])
        if "approve_front_view" not in tool_names:
            # Tries to approve its own image in the same run: must be refused.
            return AssistantTurn(text="", tool_calls=[call("approve_front_view", image_id="front-1")])
        if "generate_turnaround" not in tool_names:
            return AssistantTurn(text="", tool_calls=[call("generate_turnaround")])
        return AssistantTurn(text="等你确认。")

    use_llm(monkeypatch, policy)
    conversation_id = await start_conversation(async_client)
    body = await send(async_client, conversation_id, files=1)

    tools = {event["tool"]: event for event in body["events"] if event["type"] == "tool"}
    assert tools["approve_front_view"]["status"] == "failed"
    assert tools["generate_turnaround"]["status"] == "failed"
    assert "approved" in tools["generate_turnaround"]["error"]
    assert body["state"]["approved_front_id"] is None
    assert not [event for event in body["events"] if event["type"] == "image" and event["role"] == "turnaround"]


async def test_approve_button_then_turnaround(agent_env, async_client, monkeypatch):
    use_llm(monkeypatch, auto_front_policy)
    conversation_id = await start_conversation(async_client)
    await send(async_client, conversation_id, files=1)

    def after_approval(messages):
        if messages[-1]["role"] == "user":
            return AssistantTurn(text="好的，开始四视图。", tool_calls=[call("generate_turnaround")])
        return AssistantTurn(text="四视图完成。")

    use_llm(monkeypatch, after_approval)
    body = await send(async_client, conversation_id, action="approve_front", image_id="front-1")

    assert body["state"]["approved_front_id"] == "front-1"
    turnarounds = [image for image in body["state"]["images"] if image["role"] == "turnaround"]
    assert len(turnarounds) == 1 and turnarounds[0]["parent_id"] == "front-1"
    user_event = next(event for event in reversed(body["events"]) if event["type"] == "user_message")
    assert user_event["action"] == "approve_front"


async def test_text_approval_after_user_reply_is_allowed(agent_env, async_client, monkeypatch):
    use_llm(monkeypatch, auto_front_policy)
    conversation_id = await start_conversation(async_client)
    await send(async_client, conversation_id, files=1)

    def approve(messages):
        if messages[-1]["role"] == "user":
            return AssistantTurn(text="", tool_calls=[call("approve_front_view", image_id="front-1")])
        return AssistantTurn(text="已确认。")

    use_llm(monkeypatch, approve)
    body = await send(async_client, conversation_id, text="可以，就这张")
    assert body["state"]["approved_front_id"] == "front-1"


async def test_generation_cap_per_turn(agent_env, async_client, monkeypatch):
    def greedy(messages):
        generated = [m for m in messages if m["role"] == "tool"]
        if len(generated) < 5:
            return AssistantTurn(text="", tool_calls=[call("generate_front_view", skip_design=True)])
        return AssistantTurn(text="done")

    use_llm(monkeypatch, greedy)
    monkeypatch.setenv("AGENT_MAX_GENERATIONS_PER_TURN", "2")
    from app.core.config import get_settings

    get_settings.cache_clear()
    conversation_id = await start_conversation(async_client)
    body = await send(async_client, conversation_id, files=1)

    fronts = [image for image in body["state"]["images"] if image["role"] == "front"]
    assert len(fronts) == 2
    conversation = conversation_store.get(conversation_id)
    limited = [
        json.loads(m["content"]) for m in conversation.llm_messages if m["role"] == "tool"
    ][2:]
    assert all("limit" in result["error"] for result in limited)


async def test_message_rejected_while_running_and_empty(agent_env, async_client, monkeypatch):
    use_llm(monkeypatch, lambda messages: AssistantTurn(text="hi"))
    conversation_id = await start_conversation(async_client)
    response = await async_client.post(f"/agent/conversations/{conversation_id}/messages", data={"text": "  "})
    assert response.status_code == 400


async def test_llm_error_is_reported(agent_env, async_client, monkeypatch):
    from app.agent.llm import AgentLLMError

    class Broken:
        name = "broken"

        async def complete(self, *args, **kwargs):
            raise AgentLLMError("Agent LLM returned HTTP 401")

    monkeypatch.setattr(runner_module, "get_agent_llm", lambda: Broken())
    conversation_id = await start_conversation(async_client)
    body = await send(async_client, conversation_id, text="hello")
    assert body["status"] == "error"
    assert any(event["type"] == "error" and "401" in event["message"] for event in body["events"])


async def test_conversation_list_and_delete(agent_env, async_client, monkeypatch):
    use_llm(monkeypatch, lambda messages: AssistantTurn(text="hi"))
    conversation_id = await start_conversation(async_client)
    await send(async_client, conversation_id, text="第一条消息")
    listed = (await async_client.get("/agent/conversations")).json()
    assert listed[0]["id"] == conversation_id and listed[0]["title"] == "第一条消息"
    assert (await async_client.delete(f"/agent/conversations/{conversation_id}")).json() == {"deleted": True}
    assert (await async_client.get(f"/agent/conversations/{conversation_id}")).status_code == 404


def test_parse_openai_message_with_tool_calls_and_think():
    turn = parse_openai_message(
        {
            "content": "<think>hmm</think>好的",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "generate_front_view", "arguments": '{"instructions":"x"}'}},
                {"id": "c2", "type": "function", "function": {"name": "analyze_references", "arguments": "not json"}},
            ],
        }
    )
    assert turn.text == "好的"
    assert [(c.name, c.arguments) for c in turn.tool_calls] == [
        ("generate_front_view", {"instructions": "x"}),
        ("analyze_references", {}),
    ]


def test_parse_claude_code_reply_variants():
    fenced = parse_claude_code_reply('```json\n{"reply": "hi", "tool_calls": [{"name": "analyze_references", "arguments": {}}]}\n```')
    assert fenced.text == "hi" and fenced.tool_calls[0].name == "analyze_references"
    plain = parse_claude_code_reply("just text")
    assert plain.text == "just text" and plain.tool_calls == []


def test_render_claude_code_prompt_includes_transcript_and_files():
    from app.agent.tools import TOOL_SPECS

    prompt = render_claude_code_prompt(
        "SYSTEM",
        [
            {"role": "user", "content": "做个头壳"},
            AssistantTurn(text="", tool_calls=[call("analyze_references")]).to_message(),
            {"role": "tool", "tool_call_id": "x", "name": "analyze_references", "content": '{"ok": true}'},
        ],
        TOOL_SPECS,
        ["images/front-1.webp"],
    )
    assert "[user] 做个头壳" in prompt
    assert "[assistant called analyze_references]" in prompt
    assert "images/front-1.webp" in prompt
    assert "generate_turnaround" in prompt


async def test_openai_compatible_request_shape(monkeypatch):
    import httpx

    from app.agent import llm as llm_module
    from app.core import chat_api
    from app.agent.tools import TOOL_SPECS
    from app.core.config import get_settings

    monkeypatch.setenv("AGENT_LLM_API_KEY", "")
    monkeypatch.setenv("ARK_API_KEY", "ark-test-key")
    get_settings.cache_clear()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            text='data: {"choices": [{"delta": {"content": "好的"}}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        chat_api.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)
    )
    turn = await llm_module.OpenAICompatibleLLM().complete(
        "SYS",
        [{"role": "user", "content": "hi"}, {"role": "tool", "tool_call_id": "c", "name": "x", "content": "{}"}],
        TOOL_SPECS,
        workspace=Path("."),
        readable_files=[],
    )
    get_settings.cache_clear()

    assert turn.text == "好的"
    assert seen["url"] == "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions"
    assert seen["auth"] == "Bearer ark-test-key"
    body = seen["body"]
    assert body["model"] == "doubao-seed-2-0-pro"
    assert body["stream"] is True
    assert body["thinking"] == {"type": "disabled"}
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    assert "name" not in body["messages"][2]
    assert {tool["function"]["name"] for tool in body["tools"]} >= {"generate_front_view", "generate_turnaround"}


async def test_failed_tool_skips_rest_of_batch(agent_env, async_client, monkeypatch):
    from app.generation.backends.fixture import FixtureImageProvider
    from app.generation.backends.types import ReferenceRejectedError

    async def reject(self, request):
        raise ReferenceRejectedError("reference_adult_explicit", "not allowed")

    monkeypatch.setattr(FixtureImageProvider, "analyze_reference_details", reject)

    def batch(messages):
        if messages[-1]["role"] == "user":
            return AssistantTurn(text="", tool_calls=[call("analyze_references"), call("generate_front_view")])
        return AssistantTurn(text="这张图不能用。")

    use_llm(monkeypatch, batch)
    conversation_id = await start_conversation(async_client)
    body = await send(async_client, conversation_id, files=1)

    tools = [(event["tool"], event["status"]) for event in body["events"] if event["type"] == "tool"]
    assert tools == [("analyze_references", "failed")]
    assert body["state"]["images"] == []
    results = [json.loads(m["content"]) for m in conversation_store.get(conversation_id).llm_messages if m["role"] == "tool"]
    assert "Skipped" in results[1]["error"]


async def test_manual_version_is_saved_watermarked_with_clean_source(agent_env, async_client, monkeypatch):
    use_llm(monkeypatch, auto_front_policy)
    conversation_id = await start_conversation(async_client)
    await send(async_client, conversation_id, files=1)

    edited = io.BytesIO()
    Image.new("RGB", (80, 110), (10, 200, 30)).save(edited, format="PNG")
    response = await async_client.post(
        f"/agent/conversations/{conversation_id}/versions",
        data={"base_image_id": "front-1", "note": "eyes bigger", "recipe": json.dumps({"eyes": {"eyeSize": 0.2}})},
        files={"image": ("edit.png", edited.getvalue(), "image/png")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    manual = body["state"]["images"][-1]
    assert manual["id"] == "front-2" and manual["source"] == "manual" and manual["parent_id"] == "front-1"
    assert manual["recipe"] == {"eyes": {"eyeSize": 0.2}}
    assert body["state"]["current_front_id"] == "front-2"

    # Public copy is watermarked; the editor source endpoint serves the clean pixels.
    public = await async_client.get(manual["url"].removeprefix("/api"))
    source = await async_client.get(f"/agent/conversations/{conversation_id}/versions/front-2/source")
    assert source.status_code == 200
    clean_pixel = Image.open(io.BytesIO(source.content)).convert("RGB").getpixel((5, 105))
    public_image = Image.open(io.BytesIO(public.content)).convert("RGB")
    assert clean_pixel == (10, 200, 30)
    assert list(public_image.getdata()) != [(10, 200, 30)] * (80 * 110)
    transcript = conversation_store.get(conversation_id).llm_messages
    assert "front-2" in transcript[-1]["content"]


async def test_rename_and_upload_references_without_chat(agent_env, async_client, monkeypatch):
    conversation_id = (await async_client.post("/agent/conversations", json={"title": "猫娘头壳"})).json()["id"]
    renamed = await async_client.patch(f"/agent/conversations/{conversation_id}", json={"title": "新名字"})
    assert renamed.json()["title"] == "新名字"
    response = await async_client.post(
        f"/agent/conversations/{conversation_id}/references",
        files=[("files", ("a.png", png_bytes(), "image/png")), ("files", ("b.png", png_bytes(), "image/png"))],
    )
    assert [reference["kind"] for reference in response.json()["state"]["references"]] == ["front", "supplemental"]


async def test_local_revision_runs_as_direct_action(agent_env, async_client, monkeypatch):
    from app.generation.backends.fixture import FixtureImageProvider

    use_llm(monkeypatch, auto_front_policy)
    conversation_id = await start_conversation(async_client)
    await send(async_client, conversation_id, files=1)
    monkeypatch.setattr(FixtureImageProvider, "supports_local_revision", property(lambda self: True))

    base = Image.open(io.BytesIO((await async_client.get(
        f"/agent/conversations/{conversation_id}/versions/front-1/source"
    )).content))
    mask = Image.new("L", base.size, 0)
    mask.paste(255, (10, 10, 60, 60))
    mask_buffer = io.BytesIO()
    mask.save(mask_buffer, format="PNG")

    def no_llm(messages):
        raise AssertionError("local revision must not call the LLM")

    use_llm(monkeypatch, no_llm)
    response = await async_client.post(
        f"/agent/conversations/{conversation_id}/local-revision",
        data={"base_image_id": "front-1", "edit_note": "把发夹去掉", "lock_outside": "true"},
        files={"mask": ("mask.png", mask_buffer.getvalue(), "image/png")},
    )
    assert response.status_code == 200, response.text
    await agent_runner.wait(conversation_id)
    body = (await async_client.get(f"/agent/conversations/{conversation_id}")).json()
    tools = [(event["tool"], event["status"]) for event in body["events"] if event["type"] == "tool"]
    assert tools[-1] == ("local_revision", "succeeded")
    latest = body["state"]["images"][-1]
    assert latest["source"] == "local_revision" and latest["parent_id"] == "front-1"
    job = __import__("app.generation.job_store", fromlist=["job_store"]).job_store.get(latest["job_id"])
    assert job.prompt_payload["local_edit"]["feather_radius_px"] == 3
    assert "[editor action]" in conversation_store.get(conversation_id).llm_messages[-1]["content"]


async def test_local_revision_rejected_when_backend_unsupported(agent_env, async_client, monkeypatch):
    use_llm(monkeypatch, auto_front_policy)
    conversation_id = await start_conversation(async_client)
    await send(async_client, conversation_id, files=1)
    mask_buffer = io.BytesIO()
    Image.new("L", (800, 1100), 255).save(mask_buffer, format="PNG")
    await async_client.post(
        f"/agent/conversations/{conversation_id}/local-revision",
        data={"base_image_id": "front-1", "edit_note": "x"},
        files={"mask": ("mask.png", mask_buffer.getvalue(), "image/png")},
    )
    await agent_runner.wait(conversation_id)
    body = (await async_client.get(f"/agent/conversations/{conversation_id}")).json()
    failed = [event for event in body["events"] if event["type"] == "tool" and event["tool"] == "local_revision"]
    assert failed[0]["status"] == "failed" and "does not support" in failed[0]["error"]


async def test_annotated_revision_uses_annotated_export_as_front(agent_env, async_client, monkeypatch):
    use_llm(monkeypatch, auto_front_policy)
    conversation_id = await start_conversation(async_client)
    await send(async_client, conversation_id, files=1)
    use_llm(monkeypatch, lambda messages: (_ for _ in ()).throw(AssertionError("no LLM")))

    annotated = io.BytesIO()
    Image.new("RGB", (80, 110), "red").save(annotated, format="PNG")
    response = await async_client.post(
        f"/agent/conversations/{conversation_id}/annotated-revision",
        data={"base_image_id": "front-1", "instructions": "标注 1: 刘海剪短"},
        files={"image": ("annotated.png", annotated.getvalue(), "image/png")},
    )
    assert response.status_code == 200, response.text
    await agent_runner.wait(conversation_id)
    body = (await async_client.get(f"/agent/conversations/{conversation_id}")).json()
    latest = body["state"]["images"][-1]
    assert latest["id"] == "front-2" and latest["parent_id"] == "front-1"
    from app.generation.job_store import job_store

    job = job_store.get(latest["job_id"])
    assert job.generation_mode == "front_revision"
    assert job.prompt_payload["reference_keys"][0].startswith("front:references/agent/")


async def test_design_versions_support_manual_annotated_and_local_edits(agent_env, async_client, monkeypatch):
    from app.generation.backends.fixture import FixtureImageProvider

    use_llm(monkeypatch, auto_design_policy)
    conversation_id = await start_conversation(async_client)
    await send(async_client, conversation_id, files=1)
    use_llm(monkeypatch, lambda messages: (_ for _ in ()).throw(AssertionError("no LLM")))

    edited = io.BytesIO()
    Image.new("RGB", (80, 110), (10, 200, 30)).save(edited, format="PNG")
    manual = await async_client.post(
        f"/agent/conversations/{conversation_id}/versions",
        data={"base_image_id": "design-1", "note": "eyes bigger"},
        files={"image": ("edit.png", edited.getvalue(), "image/png")},
    )
    assert manual.json()["state"]["images"][-1]["id"] == "design-2"
    assert manual.json()["state"]["current_design_id"] == "design-2"

    response = await async_client.post(
        f"/agent/conversations/{conversation_id}/annotated-revision",
        data={"base_image_id": "design-2", "instructions": "标注 1: 刘海剪短"},
        files={"image": ("annotated.png", edited.getvalue(), "image/png")},
    )
    assert response.status_code == 200, response.text
    await agent_runner.wait(conversation_id)
    body = (await async_client.get(f"/agent/conversations/{conversation_id}")).json()
    assert body["state"]["images"][-1]["id"] == "design-3"
    assert job_for(body, "design-3").generation_mode == "character_revision"

    monkeypatch.setattr(FixtureImageProvider, "supports_local_revision", property(lambda self: True))
    base = Image.open(io.BytesIO((await async_client.get(
        f"/agent/conversations/{conversation_id}/versions/design-3/source"
    )).content))
    mask = Image.new("L", base.size, 0)
    mask.paste(255, (10, 10, 60, 60))
    mask_buffer = io.BytesIO()
    mask.save(mask_buffer, format="PNG")
    await async_client.post(
        f"/agent/conversations/{conversation_id}/local-revision",
        data={"base_image_id": "design-3", "edit_note": "去掉发夹"},
        files={"mask": ("mask.png", mask_buffer.getvalue(), "image/png")},
    )
    await agent_runner.wait(conversation_id)
    body = (await async_client.get(f"/agent/conversations/{conversation_id}")).json()
    latest = body["state"]["images"][-1]
    assert latest["id"] == "design-4" and latest["source"] == "local_revision"
    assert job_for(body, "design-4").prompt_payload["local_edit"]["subject"] == "design"
