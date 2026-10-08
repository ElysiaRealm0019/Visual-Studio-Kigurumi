from app.agent.llm import AssistantTurn
from app.agent.runner import agent_runner
from app.agent.store import conversation_store
from tests.test_agent import agent_env, call, send, start_conversation, use_llm  # noqa: F401


def replies(body) -> list[str]:
    return [event["text"] for event in body["events"] if event["type"] == "assistant_message" and not event.get("deleted")]


async def test_delete_reply_hides_event_and_drops_it_from_llm_context(agent_env, async_client, monkeypatch):
    answers = iter(["第一条回复", "第二条回复"])
    use_llm(monkeypatch, lambda messages: AssistantTurn(text=next(answers)))
    conversation_id = await start_conversation(async_client)
    await send(async_client, conversation_id, text="你好")
    body = await send(async_client, conversation_id, text="再说一句")
    first = next(event for event in body["events"] if event.get("text") == "第一条回复")

    response = await async_client.delete(f"/agent/conversations/{conversation_id}/messages/{first['seq']}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert replies(body) == ["第二条回复"]
    # The event stays (marked deleted) so sequence numbers are never reused.
    assert any(event["seq"] == first["seq"] and event["deleted"] for event in body["events"])
    llm_texts = [m["content"] for m in conversation_store.get(conversation_id).llm_messages if m["role"] == "assistant"]
    assert llm_texts == ["第二条回复"]

    missing = await async_client.delete(f"/agent/conversations/{conversation_id}/messages/{first['seq']}")
    assert missing.status_code == 404


async def test_delete_reply_with_tool_calls_keeps_the_call(agent_env, async_client, monkeypatch):
    def policy(messages):
        if messages[-1]["role"] == "user":
            return AssistantTurn(text="我先看看状态。", tool_calls=[call("no_such_tool")])
        return AssistantTurn(text="好了。")

    use_llm(monkeypatch, policy)
    conversation_id = await start_conversation(async_client)
    body = await send(async_client, conversation_id, text="开始")
    event = next(event for event in body["events"] if event.get("text") == "我先看看状态。")

    response = await async_client.delete(f"/agent/conversations/{conversation_id}/messages/{event['seq']}")
    assert response.status_code == 200
    messages = conversation_store.get(conversation_id).llm_messages
    with_calls = [m for m in messages if m["role"] == "assistant" and m.get("tool_calls")]
    assert len(with_calls) == 1 and with_calls[0]["content"] == ""
    assert any(m["role"] == "tool" for m in messages)


async def test_regenerate_reruns_last_turn(agent_env, async_client, monkeypatch):
    answers = iter(["旧回复", "新回复"])
    llm = use_llm(monkeypatch, lambda messages: AssistantTurn(text=next(answers)))
    conversation_id = await start_conversation(async_client)
    await send(async_client, conversation_id, text="出一版")

    response = await async_client.post(f"/agent/conversations/{conversation_id}/regenerate")
    assert response.status_code == 200, response.text
    assert response.json()["running"] is True
    await agent_runner.wait(conversation_id)
    body = (await async_client.get(f"/agent/conversations/{conversation_id}")).json()

    assert replies(body) == ["新回复"]
    assert len(llm.calls) == 2
    messages = conversation_store.get(conversation_id).llm_messages
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[-1]["content"] == "新回复"
    seqs = [event["seq"] for event in body["events"]]
    assert seqs == sorted(set(seqs))


async def test_regenerate_needs_a_user_turn(agent_env, async_client, monkeypatch):
    use_llm(monkeypatch, lambda messages: AssistantTurn(text="hi"))
    conversation_id = await start_conversation(async_client)
    response = await async_client.post(f"/agent/conversations/{conversation_id}/regenerate")
    assert response.status_code == 409
    assert response.json()["detail"] == "nothing_to_regenerate"
