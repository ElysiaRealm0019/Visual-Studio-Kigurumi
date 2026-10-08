"""Conversation persistence for the agent: one JSON file per conversation plus an in-memory event bus."""

import asyncio
import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.paths import resolve_repo_path

CONVERSATION_ID_PATTERN = re.compile(r"^[a-f0-9-]{36}$")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ReferenceImage:
    id: str
    key: str
    kind: str
    url: str
    file_name: str
    note: str = ""


@dataclass
class DesignImage:
    id: str
    role: str  # stage 1: "design" | "design_turnaround"; stage 2 (head shell): "front" | "turnaround"
    job_id: str
    url: str
    reference_key: str
    width: int
    height: int
    instructions: str
    created_seq: int
    parent_id: str | None = None
    source: str = "agent"  # agent | manual | local_revision
    recipe: dict[str, Any] | None = None  # editor recipe for manual versions, so they can be re-edited


@dataclass
class AgentState:
    character_session_id: str
    references: list[ReferenceImage] = field(default_factory=list)
    analysis: dict[str, Any] | None = None
    images: list[DesignImage] = field(default_factory=list)
    current_design_id: str | None = None
    approved_design_id: str | None = None
    current_front_id: str | None = None
    approved_front_id: str | None = None
    last_user_seq: int = 0

    def image(self, image_id: str | None) -> DesignImage | None:
        return next((image for image in self.images if image.id == image_id), None)


@dataclass
class Conversation:
    id: str
    created_at: str
    title: str
    locale: str
    status: str  # idle | running | error
    state: AgentState
    llm_messages: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def next_seq(self) -> int:
        return (self.events[-1]["seq"] + 1) if self.events else 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Conversation":
        state_data = dict(data["state"])
        state_data["references"] = [ReferenceImage(**item) for item in state_data.get("references") or []]
        state_data["images"] = [DesignImage(**item) for item in state_data.get("images") or []]
        return cls(
            id=data["id"],
            created_at=data["created_at"],
            title=data.get("title") or "",
            locale=data.get("locale") or "zh-CN",
            status=data.get("status") or "idle",
            state=AgentState(**state_data),
            llm_messages=list(data.get("llm_messages") or []),
            events=list(data.get("events") or []),
        )


class ConversationStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root
        self._cache: dict[str, Conversation] = {}
        self._lock = threading.RLock()
        self._conditions: dict[str, asyncio.Condition] = {}

    @property
    def root(self) -> Path:
        root = self._root or resolve_repo_path(get_settings().agent_dir)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def workspace(self, conversation_id: str) -> Path:
        path = self.root / conversation_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create(self, locale: str) -> Conversation:
        conversation_id = str(uuid.uuid4())
        conversation = Conversation(
            id=conversation_id,
            created_at=now_iso(),
            title="",
            locale=locale,
            status="idle",
            state=AgentState(character_session_id=conversation_id),
        )
        with self._lock:
            self._cache[conversation_id] = conversation
            self.save(conversation)
        return conversation

    def get(self, conversation_id: str) -> Conversation | None:
        if not CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
            return None
        with self._lock:
            cached = self._cache.get(conversation_id)
            if cached is not None:
                return cached
            path = self.root / f"{conversation_id}.json"
            if not path.is_file():
                return None
            conversation = Conversation.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if conversation.status == "running":
                # The process restarted mid-run; the run is gone.
                conversation.status = "idle"
            self._cache[conversation_id] = conversation
            return conversation

    def list(self) -> list[Conversation]:
        conversations = []
        for path in self.root.glob("*.json"):
            conversation = self.get(path.stem)
            if conversation is not None:
                conversations.append(conversation)
        return sorted(conversations, key=lambda item: item.created_at, reverse=True)

    def delete(self, conversation_id: str) -> bool:
        with self._lock:
            path = self.root / f"{conversation_id}.json"
            self._cache.pop(conversation_id, None)
            if path.is_file():
                path.unlink()
                return True
            return False

    def save(self, conversation: Conversation) -> None:
        with self._lock:
            path = self.root / f"{conversation.id}.json"
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(conversation.to_dict(), ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)

    def condition(self, conversation_id: str) -> asyncio.Condition:
        condition = self._conditions.get(conversation_id)
        if condition is None:
            condition = asyncio.Condition()
            self._conditions[conversation_id] = condition
        return condition

    async def emit(self, conversation: Conversation, event_type: str, **data: Any) -> dict[str, Any]:
        event = {"seq": conversation.next_seq, "type": event_type, "created_at": now_iso(), **data}
        conversation.events.append(event)
        self.save(conversation)
        condition = self.condition(conversation.id)
        async with condition:
            condition.notify_all()
        return event

    async def replace_event(self, conversation: Conversation, seq: int, **data: Any) -> None:
        """Update an existing event in place (e.g. tool progress) and notify listeners with a patch event."""
        for event in conversation.events:
            if event["seq"] == seq:
                event.update(data)
                break
        await self.emit(conversation, "event_patch", target_seq=seq, patch=data)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


conversation_store = ConversationStore()
