import json

import httpx
import pytest

from app.core import chat_api

THINKING_REJECTED = {
    "error": {"code": "InvalidParameter", "message": "thinking.type `disabled` is not supported by this model"}
}


@pytest.fixture(autouse=True)
def reset_rejections():
    chat_api.forget_rejected_extra_body()
    yield
    chat_api.forget_rejected_extra_body()


def install(monkeypatch, handler) -> list[dict]:
    bodies: list[dict] = []

    def recording(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return handler(request, len(bodies))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        chat_api.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(recording), **kwargs),
    )
    return bodies


def ok(text: str = "hi") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": text}}]})


async def call(extra=None):
    return await chat_api.post_chat_completion(
        "https://llm.test/v1",
        "key",
        {"model": "glm", "messages": [{"role": "user", "content": "hi"}]},
        extra_body={"thinking": {"type": "disabled"}} if extra is None else extra,
        timeout=5,
    )


async def test_retries_without_rejected_extra_fields_and_remembers(monkeypatch):
    bodies = install(
        monkeypatch, lambda request, n: httpx.Response(400, json=THINKING_REJECTED) if "thinking" in json.loads(request.content) else ok()
    )

    result = await call()

    assert result.extra_body_dropped is True
    assert result.message["content"] == "hi"
    assert [("thinking" in body) for body in bodies] == [True, False]
    assert chat_api.extra_body_rejected("https://llm.test/v1", "glm")

    second = await call()
    assert second.extra_body_dropped is False
    assert "thinking" not in bodies[-1]
    assert len(bodies) == 3


async def test_unrelated_400_is_not_retried(monkeypatch):
    bodies = install(monkeypatch, lambda request, n: httpx.Response(400, json={"error": {"message": "bad messages"}}))

    with pytest.raises(chat_api.ChatAPIError, match="HTTP 400") as error:
        await call()

    assert error.value.status == 400
    assert len(bodies) == 1


async def test_extra_fields_are_sent_when_accepted(monkeypatch):
    bodies = install(monkeypatch, lambda request, n: ok())

    result = await call()

    assert result.extra_body_dropped is False
    assert bodies[0]["thinking"] == {"type": "disabled"}


def test_parse_extra_body_protects_request_fields():
    assert chat_api.parse_extra_body('{"model": "x", "tool_choice": "none", "top_p": 0.5}') == {"top_p": 0.5}
    assert chat_api.parse_extra_body("  ") == {}
    with pytest.raises(ValueError):
        chat_api.parse_extra_body("[1]")
    with pytest.raises(ValueError):
        chat_api.parse_extra_body("{bad")


def sse(*deltas: dict) -> httpx.Response:
    lines = [f"data: {json.dumps(delta)}" for delta in deltas] + ["data: [DONE]", ""]
    return httpx.Response(200, text="\n\n".join(lines), headers={"content-type": "text/event-stream"})


async def stream_call(extra=None, on_reasoning=None):
    return await chat_api.stream_chat_completion(
        "https://llm.test/v1",
        "key",
        {"model": "glm", "messages": [{"role": "user", "content": "hi"}]},
        extra_body={"thinking": {"type": "disabled"}} if extra is None else extra,
        timeout=5,
        on_reasoning=on_reasoning,
    )


async def test_stream_accumulates_content_and_tool_calls(monkeypatch):
    bodies = install(
        monkeypatch,
        lambda request, n: sse(
            {"choices": [{"delta": {"role": "assistant", "content": "你"}}]},
            {"choices": [{"delta": {"content": "好"}}]},
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "id": "c1", "type": "function", "function": {"name": "gen", "arguments": '{"n":'}}
                            ]
                        }
                    }
                ]
            },
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]}}]},
        ),
    )

    result = await stream_call()

    assert result.extra_body_dropped is False
    assert result.message["content"] == "你好"
    assert result.message["tool_calls"][0]["id"] == "c1"
    assert result.message["tool_calls"][0]["function"] == {"name": "gen", "arguments": '{"n":1}'}
    assert bodies[0]["stream"] is True


async def test_stream_reports_reasoning_estimates(monkeypatch):
    install(
        monkeypatch,
        lambda request, n: sse(
            {"choices": [{"delta": {"reasoning_content": "x" * 1200}}]},
            {"choices": [{"delta": {"content": "ok"}}]},
        ),
    )
    seen: list[int] = []

    async def on_reasoning(estimated: int) -> None:
        seen.append(estimated)

    result = await stream_call(on_reasoning=on_reasoning)

    assert seen == [600]  # 1200 CJK-biased chars ≈ 600 tokens
    assert result.message["reasoning_content"] == "x" * 1200
    assert result.message["content"] == "ok"


async def test_stream_retries_without_rejected_extra_fields_and_remembers(monkeypatch):
    bodies = install(
        monkeypatch,
        lambda request, n: (
            httpx.Response(400, json=THINKING_REJECTED)
            if "thinking" in json.loads(request.content)
            else sse({"choices": [{"delta": {"content": "hi"}}]})
        ),
    )

    result = await stream_call()

    assert result.extra_body_dropped is True
    assert result.message["content"] == "hi"
    assert [("thinking" in body) for body in bodies] == [True, False]
    assert chat_api.extra_body_rejected("https://llm.test/v1", "glm")

    second = await stream_call()
    assert second.extra_body_dropped is False
    assert "thinking" not in bodies[-1]
    assert len(bodies) == 3
