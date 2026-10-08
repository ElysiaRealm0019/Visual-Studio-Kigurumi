"""Shared client for OpenAI-compatible /chat/completions endpoints.

Extra request fields (AGENT_LLM_EXTRA_BODY, e.g. Doubao's `thinking: disabled`) are vendor specific: other models
reject them with HTTP 400. When the error names one of those fields, the request is sent again without them and the
endpoint + model is remembered, so later calls skip the extra fields straight away.

`stream_chat_completion` reads the response as an SSE stream instead: chunks arrive while the model still thinks,
so the httpx read timeout applies between chunks rather than to the whole silent generation, and callers can show
progress while it runs.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("uvicorn.error")

PROTECTED_FIELDS = {"model", "messages", "tools", "tool_choice"}

# (base_url, model) pairs whose API rejected the extra fields. Cleared on restart or when the settings change.
_rejected_extra_body: set[tuple[str, str]] = set()

ReasoningCallback = Callable[[int], Awaitable[None]]


class ChatAPIError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ChatResult:
    message: dict[str, Any]
    extra_body_dropped: bool


def parse_extra_body(raw: str) -> dict[str, Any]:
    """Parse the extra-fields JSON; raises ValueError when it is not a JSON object."""
    if not raw.strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("must be a JSON object")
    # Never let extra fields override the conversation itself.
    return {key: value for key, value in parsed.items() if key not in PROTECTED_FIELDS}


def forget_rejected_extra_body() -> None:
    _rejected_extra_body.clear()


def extra_body_rejected(base_url: str, model: str) -> bool:
    return (base_url, model) in _rejected_extra_body


async def post_chat_completion(
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
    *,
    extra_body: dict[str, Any],
    timeout: float,
) -> ChatResult:
    model = str(payload.get("model") or "")
    key = (base_url, model)
    use_extra = bool(extra_body) and key not in _rejected_extra_body
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await _post(client, base_url, {**payload, **extra_body} if use_extra else payload, headers)
        dropped = False
        if use_extra and response.status_code == 400 and _mentions_any(response.text, extra_body):
            logger.warning(
                "Chat API rejected extra fields %s for model=%s; retrying without them", sorted(extra_body), model
            )
            _rejected_extra_body.add(key)
            response = await _post(client, base_url, payload, headers)
            dropped = True
    if response.status_code >= 400:
        raise ChatAPIError(f"HTTP {response.status_code}: {response.text[:500]}", response.status_code)
    try:
        message = response.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ChatAPIError(f"unexpected response body: {response.text[:500]}", response.status_code) from exc
    if not isinstance(message, dict):
        raise ChatAPIError(f"unexpected response body: {response.text[:500]}", response.status_code)
    return ChatResult(message=message, extra_body_dropped=dropped)


async def _post(
    client: httpx.AsyncClient, base_url: str, body: dict[str, Any], headers: dict[str, str]
) -> httpx.Response:
    try:
        return await client.post(f"{base_url}/chat/completions", json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise ChatAPIError(f"request failed: {exc.__class__.__name__}: {exc}") from exc


async def stream_chat_completion(
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
    *,
    extra_body: dict[str, Any],
    timeout: float,
    on_reasoning: ReasoningCallback | None = None,
) -> ChatResult:
    """POST /chat/completions with `stream: true` and reassemble the SSE deltas into one message.

    `on_reasoning` is awaited with a running estimate of the reasoning tokens (CJK-biased chars // 2) on every
    reasoning delta; callers throttle milestone updates themselves.
    """
    model = str(payload.get("model") or "")
    key = (base_url, model)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    stream_payload = {**payload, "stream": True}
    use_extra = bool(extra_body) and key not in _rejected_extra_body
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await _send_stream(
            client, base_url, {**stream_payload, **extra_body} if use_extra else stream_payload, headers
        )
        dropped = False
        if use_extra and response.status_code == 400:
            await response.aread()
            if _mentions_any(response.text, extra_body):
                logger.warning(
                    "Chat API rejected extra fields %s for model=%s; retrying without them", sorted(extra_body), model
                )
                _rejected_extra_body.add(key)
                response = await _send_stream(client, base_url, payload, headers)
                dropped = True
        if response.status_code >= 400:
            await response.aread()
            raise ChatAPIError(f"HTTP {response.status_code}: {response.text[:500]}", response.status_code)
        try:
            message = await _consume_stream(response, on_reasoning)
        except httpx.HTTPError as exc:
            raise ChatAPIError(f"stream failed: {exc.__class__.__name__}: {exc}") from exc
        finally:
            await response.aclose()
    return ChatResult(message=message, extra_body_dropped=dropped)


async def _send_stream(
    client: httpx.AsyncClient, base_url: str, body: dict[str, Any], headers: dict[str, str]
) -> httpx.Response:
    request = client.build_request("POST", f"{base_url}/chat/completions", json=body, headers=headers)
    try:
        return await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        raise ChatAPIError(f"request failed: {exc.__class__.__name__}: {exc}") from exc


async def _consume_stream(response: httpx.Response, on_reasoning: ReasoningCallback | None) -> dict[str, Any]:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_chars = 0
    tool_calls: dict[int, dict[str, Any]] = {}
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue  # SSE comments / keep-alives
        data = line[len("data:") :].strip()
        if not data:
            continue
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except ValueError:
            continue
        choices = chunk.get("choices") or []
        delta = (choices[0].get("delta") if choices else None) or {}
        delta_content = delta.get("content")
        if isinstance(delta_content, str) and delta_content:
            content_parts.append(delta_content)
        delta_reasoning = delta.get("reasoning_content")
        if isinstance(delta_reasoning, str) and delta_reasoning:
            reasoning_parts.append(delta_reasoning)
            reasoning_chars += len(delta_reasoning)
            if on_reasoning is not None:
                await on_reasoning(reasoning_chars // 2)
        for raw in delta.get("tool_calls") or []:
            entry = tool_calls.setdefault(
                int(raw.get("index") or 0), {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            )
            if raw.get("id"):
                entry["id"] = raw["id"]
            if raw.get("type"):
                entry["type"] = raw["type"]
            function = raw.get("function") or {}
            if function.get("name"):
                entry["function"]["name"] += str(function["name"])
            if function.get("arguments"):
                entry["function"]["arguments"] += str(function["arguments"])
    message: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
    return message


def _mentions_any(text: str, fields: dict[str, Any]) -> bool:
    lowered = text.lower()
    return any(field.lower() in lowered for field in fields)


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):  # some APIs return content parts
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content or "")
