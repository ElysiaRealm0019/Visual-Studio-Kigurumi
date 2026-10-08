"""Shared client for OpenAI-compatible /chat/completions endpoints.

Extra request fields (AGENT_LLM_EXTRA_BODY, e.g. Doubao's `thinking: disabled`) are vendor specific: other models
reject them with HTTP 400. When the error names one of those fields, the request is sent again without them and the
endpoint + model is remembered, so later calls skip the extra fields straight away.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("uvicorn.error")

PROTECTED_FIELDS = {"model", "messages", "tools", "tool_choice"}

# (base_url, model) pairs whose API rejected the extra fields. Cleared on restart or when the settings change.
_rejected_extra_body: set[tuple[str, str]] = set()


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


def _mentions_any(text: str, fields: dict[str, Any]) -> bool:
    lowered = text.lower()
    return any(field.lower() in lowered for field in fields)


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):  # some APIs return content parts
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content or "")
