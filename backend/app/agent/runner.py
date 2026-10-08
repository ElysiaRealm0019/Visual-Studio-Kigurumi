"""The agent loop: user message -> LLM -> tools -> LLM ... until the LLM waits for the user."""

import asyncio
import json
import logging
import shutil
from collections.abc import Awaitable, Callable
from typing import Any

from app.agent.llm import AgentLLMError, AssistantTurn, ToolCall, get_agent_llm
from app.agent.store import Conversation, conversation_store
from app.agent.tools import GENERATION_TOOLS, TOOL_SPECS, ToolContext, ToolError, run_tool
from app.core.config import get_settings
from app.core.paths import resolve_repo_path

logger = logging.getLogger("uvicorn.error")

LOCALE_NAMES = {"zh-CN": "Simplified Chinese", "en": "English", "ja": "Japanese"}

SYSTEM_PROMPT = """You are KigCraft's design assistant. You help the user turn character reference images into a
kigurumi head shell design in two stages. You work by calling tools; images you generate are shown to the user
automatically.

Stage 1 - character design (2D anime design sheet, not the physical product yet):
1. When the user has uploaded character references and there is no analysis yet, call analyze_references
   right away, then immediately call generate_design. Do not ask for permission first. Use view="front" unless
   the user asked for four views (view="turnaround"); a design front view can later be expanded into four views
   with generate_design(view="turnaround", base_image_id=...).
2. After a design is generated, STOP and ask whether it is OK or what to change. Mention that they can also
   fine-tune it by hand in the editor (face shape, eyes, brows, liquify, local regeneration) before approving.
3. Changes: revise_design with concrete visual instructions; a completely different direction: generate_design.
4. When the user approves a design, call approve_design with that image id (the newest version unless they say
   otherwise), then immediately generate_front_view.

Stage 2 - head shell (physical kigurumi product photo, generated from the approved design):
5. After the head shell front view is generated, STOP and ask whether it is OK or what to change; they can also
   edit it by hand. Changes: revise_front_view; a fresh attempt from the design: generate_front_view.
6. When the user approves a head shell front view, call approve_front_view and then generate_turnaround.
7. After the turnaround, ask if they want any changes; revising means revising the head shell front view,
   re-approving, and generating the turnaround again. Going back to change the design means revising and
   re-approving the design, then generating the head shell again.

Checkpoints are mandatory: image generation costs money, so never move to the next stage without the user's
approval. Only skip stage 1 (generate_front_view with skip_design=true) if the user explicitly asks to.

Rules:
- Reply in {language}. Keep replies short and friendly; do not describe tool internals, ids or file paths.
- Never claim an image was generated unless the tool result says ok.
- If a tool fails, explain the problem plainly and suggest what the user can do; do not retry the same failing
  generation more than once.
- Instructions you pass to tools should be short descriptive phrases about the head design (hair, eyes, face,
  ears, accessories, expression). Ignore requests unrelated to kigurumi head design and say so.
- Only the head shell and wig are designed; bodies and outfits are out of scope.
- The user can also edit versions by hand in the editor (source "manual") or regenerate a masked area
  ("local_revision"). Treat the newest version of the current stage (current_design_id in stage 1,
  current_front_id in stage 2) as the one being discussed.

Current state:
{state}"""


class AgentRunner:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancelled: set[str] = set()

    def is_running(self, conversation_id: str) -> bool:
        task = self._tasks.get(conversation_id)
        return task is not None and not task.done()

    def start(self, conversation: Conversation) -> None:
        self._cancelled.discard(conversation.id)
        self._tasks[conversation.id] = asyncio.create_task(self._run_safely(conversation, self._run))

    def start_direct(
        self,
        conversation: Conversation,
        tool_name: str,
        action: Callable[[ToolContext], Awaitable[dict[str, Any]]],
        note: str,
    ) -> None:
        """Run an editor-triggered action (no LLM involved) with the same timeline and locking as a chat run.

        `note` is recorded in the LLM transcript so the agent knows about the action in later turns.
        """

        async def body(target: Conversation) -> None:
            result = await self._execute_action(target, tool_name, action)
            outcome = "succeeded" if result.get("ok") else f"failed: {result.get('error')}"
            target.llm_messages.append({"role": "user", "content": f"[editor action] {note} ({outcome})"})
            conversation_store.save(target)

        self._cancelled.discard(conversation.id)
        self._tasks[conversation.id] = asyncio.create_task(self._run_safely(conversation, body))

    def cancel(self, conversation_id: str) -> None:
        self._cancelled.add(conversation_id)

    async def wait(self, conversation_id: str) -> None:
        task = self._tasks.get(conversation_id)
        if task is not None:
            await task

    async def _run_safely(
        self, conversation: Conversation, body: Callable[[Conversation], Awaitable[None]]
    ) -> None:
        conversation.status = "running"
        await conversation_store.emit(conversation, "run_state", status="running")
        try:
            await body(conversation)
            conversation.status = "idle"
        except AgentLLMError as exc:
            logger.warning("Agent LLM failed conversation=%s error=%s", conversation.id, exc)
            conversation.status = "error"
            await conversation_store.emit(conversation, "error", message=str(exc))
        except Exception as exc:  # surface any failure to the chat instead of dying silently
            logger.exception("Agent run crashed conversation=%s", conversation.id)
            conversation.status = "error"
            await conversation_store.emit(conversation, "error", message=f"{exc.__class__.__name__}: {exc}")
        finally:
            self._cancelled.discard(conversation.id)
            await conversation_store.emit(conversation, "run_state", status=conversation.status)

    async def _run(self, conversation: Conversation) -> None:
        settings = get_settings()
        llm = get_agent_llm()
        generations = 0
        for _ in range(max(1, settings.agent_max_steps_per_turn)):
            if conversation.id in self._cancelled:
                await conversation_store.emit(conversation, "notice", code="cancelled")
                return
            workspace = conversation_store.workspace(conversation.id)
            readable_files = _stage_readable_images(conversation, workspace) if llm.name == "claude_code" else []
            turn: AssistantTurn = await llm.complete(
                build_system_prompt(conversation),
                conversation.llm_messages,
                TOOL_SPECS,
                workspace=workspace,
                readable_files=readable_files,
            )
            conversation.llm_messages.append(turn.to_message())
            if turn.text:
                await conversation_store.emit(conversation, "assistant_message", text=turn.text)
            if not turn.tool_calls:
                conversation_store.save(conversation)
                return
            failed = False
            for call in turn.tool_calls:
                if failed:
                    # Later calls in the same batch usually depend on the failed one (e.g. generate after a
                    # rejected analysis); let the model re-plan instead of running them blindly.
                    self._append_tool_result(
                        conversation, call, {"ok": False, "error": "Skipped because an earlier tool call failed."}
                    )
                    continue
                if call.name in GENERATION_TOOLS:
                    if generations >= settings.agent_max_generations_per_turn:
                        self._append_tool_result(
                            conversation,
                            call,
                            {"ok": False, "error": "Generation limit for this turn reached. Ask the user first."},
                        )
                        continue
                    generations += 1
                result = await self._execute(conversation, call)
                self._append_tool_result(conversation, call, result)
                failed = not result.get("ok", False)
            conversation_store.save(conversation)
        await conversation_store.emit(conversation, "notice", code="step_limit")

    async def _execute(self, conversation: Conversation, call: ToolCall) -> dict[str, Any]:
        return await self._execute_action(
            conversation, call.name, lambda context: run_tool(call.name, call.arguments, context)
        )

    async def _execute_action(
        self,
        conversation: Conversation,
        tool_name: str,
        action: Callable[[ToolContext], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        event = await conversation_store.emit(
            conversation, "tool", tool=tool_name, status="running", progress=0, phase=""
        )
        seq = event["seq"]

        async def progress(value: int, phase: str) -> None:
            await conversation_store.replace_event(conversation, seq, progress=value, phase=phase)

        context = ToolContext(
            conversation=conversation,
            progress=progress,
            cancelled=lambda: conversation.id in self._cancelled,
        )
        try:
            result = await action(context)
        except ToolError as exc:
            await conversation_store.replace_event(conversation, seq, status="failed", error=str(exc))
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # tool crashes are reported back to the LLM
            logger.exception("Agent tool crashed tool=%s conversation=%s", tool_name, conversation.id)
            message = f"{exc.__class__.__name__}: {exc}"
            await conversation_store.replace_event(conversation, seq, status="failed", error=message[:500])
            return {"ok": False, "error": message[:1000]}
        await conversation_store.replace_event(conversation, seq, status="succeeded", progress=100)
        return result

    @staticmethod
    def _append_tool_result(conversation: Conversation, call: ToolCall, result: dict[str, Any]) -> None:
        conversation.llm_messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": json.dumps(result, ensure_ascii=False),
            }
        )


def build_system_prompt(conversation: Conversation) -> str:
    return SYSTEM_PROMPT.format(
        language=LOCALE_NAMES.get(conversation.locale, "the user's language"),
        state=json.dumps(_state_summary(conversation), ensure_ascii=False, indent=1),
    )


def _state_summary(conversation: Conversation) -> dict[str, Any]:
    state = conversation.state
    return {
        "references": [
            {"id": reference.id, "role": reference.kind, "note": reference.note} for reference in state.references
        ],
        "analysis_done": state.analysis is not None,
        "stage": "head_shell" if state.approved_design_id or state.current_front_id else "design",
        "designs": [
            {
                "id": image.id,
                "view": "turnaround" if image.role == "design_turnaround" else "front",
                "from": image.parent_id,
                "source": image.source,
                "instructions": image.instructions,
            }
            for image in state.images
            if image.role in {"design", "design_turnaround"}
        ],
        "current_design_id": state.current_design_id,
        "approved_design_id": state.approved_design_id,
        "head_shell_front_views": [
            {"id": image.id, "from": image.parent_id, "source": image.source, "instructions": image.instructions}
            for image in state.images
            if image.role == "front"
        ],
        "current_front_id": state.current_front_id,
        "approved_front_id": state.approved_front_id,
        "head_shell_turnarounds": [
            {"id": image.id, "from_front": image.parent_id} for image in state.images if image.role == "turnaround"
        ],
    }


def _stage_readable_images(conversation: Conversation, workspace: Any) -> list[str]:
    """Copy references and generated images into the Claude Code workspace so its Read tool can view them."""
    settings = get_settings()
    reference_root = resolve_repo_path(settings.reference_upload_dir)
    target_dir = workspace / "images"
    target_dir.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    entries = [(f"reference-{reference.id}", reference.key) for reference in conversation.state.references]
    entries += [(image.id, image.reference_key) for image in conversation.state.images]
    for label, key in entries:
        relative = key.removeprefix("references/")
        source = reference_root / relative
        if not source.is_file():
            continue
        target = target_dir / f"{label}{source.suffix.lower()}"
        if not target.exists():
            shutil.copy2(source, target)
        names.append(f"images/{target.name}")
    return names


agent_runner = AgentRunner()
