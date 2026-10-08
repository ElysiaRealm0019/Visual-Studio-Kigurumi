"""Connection test for the OpenAI-compatible LLM roles on the settings page.

One small request per role checks that the endpoint, key and model work, whether the extra request fields are
accepted, and the capability the role needs: tool calling for the chat assistant, image input for analysis.
"""

import base64
import io
import time
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel

from app.agent.llm import ToolSpec, parse_openai_message, resolve_agent_api_key, tool_payload
from app.core import chat_api
from app.generation.backends.openai_compatible import analysis_extra_body, resolve_analysis_endpoint

CheckStatus = Literal["ok", "warn", "fail"]

PING_TOOL = ToolSpec(
    name="ping",
    description="Connectivity test. Call it when asked.",
    parameters={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
)


class ProbeCheck(BaseModel):
    id: str  # connection | extra_body | tools | vision
    status: CheckStatus
    detail: str = ""


class ProbeOut(BaseModel):
    ok: bool
    model: str
    base_url: str
    latency_ms: int | None = None
    checks: list[ProbeCheck]


async def probe_agent(settings: Any) -> ProbeOut:
    base_url = settings.agent_llm_base_url.strip().rstrip("/")
    model = settings.agent_llm_model.strip()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a connectivity test."},
            {"role": "user", "content": "Call the ping tool with ok=true. Do not answer with text."},
        ],
        "tools": [tool_payload(PING_TOOL)],
        "tool_choice": "auto",
    }
    return await _probe(
        base_url,
        resolve_agent_api_key(settings),
        model,
        payload,
        _extra_body(settings),
        float(settings.agent_llm_timeout_seconds),
        _check_tool_call,
    )


async def probe_analysis(settings: Any) -> ProbeOut:
    base_url, api_key, model = resolve_analysis_endpoint(settings)
    content = [
        {"type": "text", "text": "What colour fills this image? Answer with one English word."},
        {"type": "image_url", "image_url": {"url": _red_square()}},
    ]
    payload = {"model": model, "messages": [{"role": "user", "content": content}]}
    return await _probe(
        base_url,
        api_key,
        model,
        payload,
        analysis_extra_body(settings),
        float(settings.codex_detail_analysis_timeout_seconds),
        _check_vision,
    )


async def _probe(
    base_url: str,
    api_key: str,
    model: str,
    payload: dict[str, Any],
    extra_body: dict[str, Any],
    timeout: float,
    check_capability,
) -> ProbeOut:
    if not base_url or not model:
        return _failed(base_url, model, "missing_endpoint")
    # Test from scratch: an earlier rejection of the extra fields must not hide the answer.
    chat_api.forget_rejected_extra_body()
    started = time.perf_counter()
    try:
        result = await chat_api.post_chat_completion(base_url, api_key, payload, extra_body=extra_body, timeout=timeout)
    except chat_api.ChatAPIError as exc:
        return _failed(base_url, model, _classify(exc), str(exc))
    latency_ms = round((time.perf_counter() - started) * 1000)

    checks = [ProbeCheck(id="connection", status="ok")]
    if extra_body:
        checks.append(
            ProbeCheck(
                id="extra_body",
                status="warn" if result.extra_body_dropped else "ok",
                detail=", ".join(sorted(extra_body)),
            )
        )
    checks.append(check_capability(result.message))
    return ProbeOut(
        ok=all(check.status != "fail" for check in checks),
        model=model,
        base_url=base_url,
        latency_ms=latency_ms,
        checks=checks,
    )


def _check_tool_call(message: dict[str, Any]) -> ProbeCheck:
    turn = parse_openai_message(message)
    if any(call.name == PING_TOOL.name for call in turn.tool_calls):
        return ProbeCheck(id="tools", status="ok")
    return ProbeCheck(id="tools", status="fail", detail=turn.text[:200])


def _check_vision(message: dict[str, Any]) -> ProbeCheck:
    answer = chat_api.message_text(message).strip()
    lowered = answer.lower()
    if "red" in lowered or "红" in answer:
        return ProbeCheck(id="vision", status="ok", detail=answer[:80])
    return ProbeCheck(id="vision", status="warn", detail=answer[:200])


def _classify(exc: chat_api.ChatAPIError) -> str:
    text = str(exc).lower()
    if exc.status in (401, 403):
        return "auth"
    if exc.status == 404:
        return "not_found"
    if exc.status is None:
        return "unreachable"
    if any(word in text for word in ("image", "vision", "multimodal", "image_url")):
        return "no_vision"
    return "error"


def _failed(base_url: str, model: str, reason: str, detail: str = "") -> ProbeOut:
    return ProbeOut(
        ok=False,
        model=model,
        base_url=base_url,
        checks=[ProbeCheck(id="connection", status="fail", detail=f"{reason}: {detail}" if detail else reason)],
    )


def _extra_body(settings: Any) -> dict[str, Any]:
    try:
        return chat_api.parse_extra_body(settings.agent_llm_extra_body)
    except ValueError:
        return {}


def _red_square() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (220, 30, 30)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
