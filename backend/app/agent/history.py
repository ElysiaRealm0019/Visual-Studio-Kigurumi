"""Deleting assistant replies and re-running the last turn.

Timeline events are never removed: a deleted reply is marked `deleted` (and listeners get a patch), so sequence numbers
are never reused. The matching assistant message in the LLM transcript is located by text: the n-th visible reply with
a given text is the n-th assistant message with that content.
"""

from typing import Any

from app.agent.store import Conversation, conversation_store

EDITOR_ACTION_PREFIX = "[editor action]"


class HistoryError(ValueError):
    pass


def _visible_replies(conversation: Conversation) -> list[dict[str, Any]]:
    return [
        event
        for event in conversation.events
        if event["type"] == "assistant_message" and not event.get("deleted")
    ]


def _llm_index_for_reply(conversation: Conversation, event: dict[str, Any]) -> int | None:
    text = event.get("text") or ""
    occurrence = sum(
        1 for other in _visible_replies(conversation) if other["seq"] < event["seq"] and (other.get("text") or "") == text
    )
    for index, message in enumerate(conversation.llm_messages):
        if message.get("role") == "assistant" and (message.get("content") or "") == text:
            if occurrence == 0:
                return index
            occurrence -= 1
    return None


async def delete_reply(conversation: Conversation, seq: int) -> None:
    event = next((item for item in conversation.events if item["seq"] == seq), None)
    if event is None or event["type"] != "assistant_message" or event.get("deleted"):
        raise HistoryError("message_not_found")
    index = _llm_index_for_reply(conversation, event)
    if index is not None:
        message = conversation.llm_messages[index]
        if message.get("tool_calls"):
            # Tool results answer these calls; dropping the message would orphan them, so only the text goes.
            message["content"] = ""
        else:
            del conversation.llm_messages[index]
    await conversation_store.replace_event(conversation, seq, deleted=True)


async def prepare_regenerate(conversation: Conversation) -> None:
    """Rewind the transcript to the last user message and hide that turn's replies; the caller starts the run."""
    user_index = next(
        (
            index
            for index in range(len(conversation.llm_messages) - 1, -1, -1)
            if conversation.llm_messages[index].get("role") == "user"
        ),
        None,
    )
    if user_index is None:
        raise HistoryError("nothing_to_regenerate")
    content = conversation.llm_messages[user_index].get("content")
    if isinstance(content, str) and content.startswith(EDITOR_ACTION_PREFIX):
        raise HistoryError("nothing_to_regenerate")
    last_user_seq = max(
        (event["seq"] for event in conversation.events if event["type"] == "user_message"), default=None
    )
    if last_user_seq is None:
        raise HistoryError("nothing_to_regenerate")

    del conversation.llm_messages[user_index + 1 :]
    # Generated images stay: they are real versions in the project, and the state summary still lists them.
    for event in conversation.events:
        if event["seq"] > last_user_seq and event["type"] in {"assistant_message", "error"} and not event.get("deleted"):
            await conversation_store.replace_event(conversation, event["seq"], deleted=True)
    conversation_store.save(conversation)
