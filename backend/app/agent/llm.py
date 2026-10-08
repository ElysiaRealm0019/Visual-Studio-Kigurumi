"""Chat LLM backends for the conversational agent.

Messages use the OpenAI chat format internally (role/content/tool_calls/tool_call_id). The OpenAI-compatible
backend sends them as-is with native function calling; the Claude Code backend renders the transcript as text
and asks for a JSON reply, because the CLI has no client-side tool calling.
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.core import chat_api
from app.core.config import get_settings
from app.generation.backends.claude_code import run_claude_code

logger = logging.getLogger("uvicorn.error")

AGENT_LLM_BACKENDS = {"openai_compatible", "claude_code"}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AssistantTurn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.text or ""}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
                }
                for call in self.tool_calls
            ]
        return message


class AgentLLMError(RuntimeError):
    pass


class AgentLLM(Protocol):
    name: str

    async def complete(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        workspace: Path,
        readable_files: list[str],
    ) -> AssistantTurn: ...


def get_agent_llm() -> AgentLLM:
    provider = get_settings().agent_llm_provider.strip().lower() or "openai_compatible"
    if provider == "openai_compatible":
        return OpenAICompatibleLLM()
    if provider == "claude_code":
        return ClaudeCodeAgentLLM()
    raise AgentLLMError(
        f"Unsupported AGENT_LLM_PROVIDER={provider!r}; expected one of {sorted(AGENT_LLM_BACKENDS)}"
    )


class OpenAICompatibleLLM:
    name = "openai_compatible"

    async def complete(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        workspace: Path,
        readable_files: list[str],
    ) -> AssistantTurn:
        settings = get_settings()
        base_url = settings.agent_llm_base_url.strip().rstrip("/")
        api_key = resolve_agent_api_key(settings)
        model = settings.agent_llm_model.strip()
        if not base_url or not model:
            raise AgentLLMError("AGENT_LLM_BASE_URL and AGENT_LLM_MODEL must be set for the openai_compatible agent")
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}, *map(_wire_message, messages)],
        }
        if tools:
            payload["tools"] = [tool_payload(tool) for tool in tools]
            payload["tool_choice"] = "auto"
        try:
            result = await chat_api.post_chat_completion(
                base_url,
                api_key,
                payload,
                extra_body=parse_extra_body(settings.agent_llm_extra_body),
                timeout=float(settings.agent_llm_timeout_seconds),
            )
        except chat_api.ChatAPIError as exc:
            raise AgentLLMError(f"Agent LLM {exc}") from exc
        return parse_openai_message(result.message)


def tool_payload(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
    }


def resolve_agent_api_key(settings: Any) -> str:
    key = settings.agent_llm_api_key.strip()
    if key:
        return key
    # Same Volcengine Ark Agent Plan endpoint as the image backend: reuse its dedicated key.
    if settings.agent_llm_base_url.strip().rstrip("/") == settings.ark_base_url.strip().rstrip("/"):
        return settings.ark_api_key.strip()
    return ""


def parse_extra_body(raw: str) -> dict[str, Any]:
    try:
        return chat_api.parse_extra_body(raw)
    except ValueError as exc:
        raise AgentLLMError(f"AGENT_LLM_EXTRA_BODY is not a valid JSON object: {exc}") from exc


def _wire_message(message: dict[str, Any]) -> dict[str, Any]:
    # The stored tool name is only for the Claude Code transcript; some providers reject extra fields.
    if message.get("role") == "tool":
        return {key: value for key, value in message.items() if key != "name"}
    return message


def parse_openai_message(message: dict[str, Any]) -> AssistantTurn:
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    text = _strip_reasoning(str(content or ""))
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        calls.append(
            ToolCall(
                id=str(raw.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
                name=name,
                arguments=_parse_arguments(function.get("arguments")),
            )
        )
    return AssistantTurn(text=text.strip(), tool_calls=calls)


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _strip_reasoning(text: str) -> str:
    # Some OpenAI-compatible reasoning models inline their thinking in <think> tags.
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)


CLAUDE_CODE_REPLY_FORMAT = """Reply with ONLY one JSON object and nothing else:
{"reply": "<text shown to the user; may be empty while you call tools>",
 "tool_calls": [{"name": "<tool name>", "arguments": {...}}]}
Use an empty tool_calls list when you are done and waiting for the user. Never invent tool names."""


class ClaudeCodeAgentLLM:
    name = "claude_code"

    async def complete(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        workspace: Path,
        readable_files: list[str],
    ) -> AssistantTurn:
        prompt = render_claude_code_prompt(system_prompt, messages, tools, readable_files)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "agent-prompt.md").write_text(prompt, encoding="utf-8")
        settings = get_settings()
        model = settings.agent_claude_code_model.strip()
        result = await run_claude_code(
            prompt,
            workspace=workspace,
            label="agent-turn",
            job_id=f"agent-{workspace.name}",
            model=model or None,
        )
        return parse_claude_code_reply(result.text)


def render_claude_code_prompt(
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[ToolSpec],
    readable_files: list[str],
) -> str:
    lines = [system_prompt, "", "## Tools"]
    for tool in tools:
        lines.append(f"- {tool.name}: {tool.description}")
        lines.append(f"  arguments schema: {json.dumps(tool.parameters, ensure_ascii=False)}")
    if readable_files:
        lines += ["", "## Images you can inspect with the Read tool (paths relative to the current directory)"]
        lines += [f"- {name}" for name in readable_files]
    lines += ["", "## Conversation so far"]
    for message in messages:
        role = message.get("role")
        if role == "user":
            lines.append(f"[user] {message.get('content') or ''}")
        elif role == "assistant":
            if message.get("content"):
                lines.append(f"[assistant] {message['content']}")
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                lines.append(f"[assistant called {function.get('name')}] {function.get('arguments') or '{}'}")
        elif role == "tool":
            lines.append(f"[tool result] {message.get('content') or ''}")
    lines += ["", "## Your turn", CLAUDE_CODE_REPLY_FORMAT]
    return "\n".join(lines)


def parse_claude_code_reply(text: str) -> AssistantTurn:
    payload = _extract_json_object(text)
    if payload is None:
        # Fall back to treating the whole answer as a plain reply rather than failing the turn.
        return AssistantTurn(text=text.strip())
    calls = [
        ToolCall(
            id=f"call_{uuid.uuid4().hex[:12]}",
            name=str(item.get("name") or "").strip(),
            arguments=item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
        )
        for item in payload.get("tool_calls") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    return AssistantTurn(text=str(payload.get("reply") or "").strip(), tool_calls=calls)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, flags=re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
