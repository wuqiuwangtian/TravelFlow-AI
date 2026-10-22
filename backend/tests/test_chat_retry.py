import json

import pytest
from conftest import FakeLLM
from test_itinerary_api import generate

from app.agents.checkpoint import thread_config
from app.core.errors import LLMUnavailable
from app.services.chat_service import ChatService


async def test_same_request_replays_across_endpoints_and_after_later_messages(environment):
    client, store, graph, _ = environment
    sid, bid, _ = await generate(client)
    payload = {
        "session_id": sid,
        "branch_id": bid,
        "message": "第1天午餐花了86元",
        "request_id": "expense-retry-01",
    }
    first = (await client.post("/api/chat", json=payload)).json()
    assert first["version_id"]
    assert (await client.post("/api/chat", json=payload)).json() == first
    streaming = await client.post("/api/chat/stream", json=payload)
    replay = json.loads(streaming.text.split("event: done\ndata: ")[1].split("\n\n")[0])
    assert replay == first
    later = {**payload, "message": "第2天地铁花了5元", "request_id": "expense-retry-02"}
    await client.post("/api/chat", json=later)
    assert (await client.post("/api/chat", json=payload)).json() == first
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert len(current.expenses) == 2 and current.expenses[1].amount_cents == 500
    assert len(await store.list_versions(bid)) == 3
    different = await client.post("/api/chat", json={**payload, "message": "午餐花了100元"})
    assert different.status_code == 400
    assert len(await store.list_versions(bid)) == 3


@pytest.mark.parametrize("missing_version", [False, True])
async def test_checkpoint_recovers_after_result_recording_is_interrupted(
    environment, monkeypatch, missing_version
):
    client, store, graph, _ = environment
    sid, bid, _ = await generate(client)
    payload = {
        "session_id": sid,
        "branch_id": bid,
        "message": "第1天午餐花了86元",
        "request_id": "result-interrupted",
    }
    if missing_version:
        target, name = ChatService, "record_version"
    else:
        target, name = store, "save_chat_receipt"
    original = getattr(target, name)

    async def interrupted(*args, **kwargs):
        raise LLMUnavailable("模拟完成后连接中断")

    monkeypatch.setattr(target, name, interrupted)
    assert (await client.post("/api/chat", json=payload)).status_code == 502
    snapshot = await graph.aget_state(thread_config(bid))
    assert len(snapshot.values["itinerary"].expenses) == 1
    assert snapshot.values["completed_chat"]["id"] == payload["request_id"]
    monkeypatch.setattr(target, name, original)
    result = await client.post("/api/chat", json=payload)
    assert result.status_code == 200
    assert len(await store.list_versions(bid)) == 2
    assert len((await graph.aget_state(thread_config(bid))).values["itinerary"].expenses) == 1


async def test_failed_model_can_retry_same_request_without_duplicate_history(
    environment, monkeypatch
):
    client, store, graph, _ = environment
    sid, bid, _ = await generate(client)
    payload = {
        "session_id": sid,
        "branch_id": bid,
        "message": "第1天午餐花了86元，后面轻松一点",
        "request_id": "model-retry-test",
    }
    original = FakeLLM.structured

    async def offline(*args, **kwargs):
        raise LLMUnavailable("模拟模型不可用")

    monkeypatch.setattr(FakeLLM, "structured", offline)
    assert (await client.post("/api/chat", json=payload)).status_code == 502
    assert len(await store.list_versions(bid)) == 1
    monkeypatch.setattr(FakeLLM, "structured", original)
    assert (await client.post("/api/chat", json=payload)).status_code == 200
    snapshot = await graph.aget_state(thread_config(bid))
    assert len(snapshot.values["itinerary"].expenses) == 1
    assert (
        sum(
            m.type == "human" and m.content == payload["message"]
            for m in snapshot.values["messages"]
        )
        == 1
    )


async def test_retry_is_branch_scoped_and_receipts_are_deleted_with_the_trip(environment):
    client, store, graph, _ = environment
    sid, bid, _ = await generate(client)
    payload = {
        "session_id": sid,
        "branch_id": bid,
        "message": "第1天午餐花了86元",
        "request_id": "branch-scoped-id",
    }
    await client.post("/api/chat", json=payload)
    branch = await client.post(
        f"/api/sessions/{sid}/branches", json={"title": "分支", "parent_branch_id": bid}
    )
    new_bid = next(b["id"] for b in branch.json()["items"] if b["title"] == "分支")
    await client.post("/api/chat", json={**payload, "branch_id": new_bid})
    assert len((await graph.aget_state(thread_config(new_bid))).values["itinerary"].expenses) == 2
    assert await store.get_chat_receipt(bid, payload["request_id"])
    assert await store.get_chat_receipt(new_bid, payload["request_id"])
    await store.delete_session(sid)
    assert await store.get_chat_receipt(bid, payload["request_id"]) is None
    assert await store.get_chat_receipt(new_bid, payload["request_id"]) is None


async def test_new_message_recovers_an_unacknowledged_completed_request(environment, monkeypatch):
    client, store, graph, _ = environment
    sid, bid, _ = await generate(client)
    payload = {
        "session_id": sid,
        "branch_id": bid,
        "message": "第1天午餐花了86元",
        "request_id": "unacknowledged-old",
    }
    original = store.save_chat_receipt

    async def fail(*args, **kwargs):
        raise LLMUnavailable("模拟回执未保存")

    monkeypatch.setattr(store, "save_chat_receipt", fail)
    assert (await client.post("/api/chat", json=payload)).status_code == 502
    monkeypatch.setattr(store, "save_chat_receipt", original)
    fresh = {**payload, "request_id": "unacknowledged-new", "message": "第2天地铁花了5元"}
    assert (await client.post("/api/chat", json=fresh)).status_code == 200
    assert (await client.post("/api/chat", json=payload)).status_code == 200
    assert len((await graph.aget_state(thread_config(bid))).values["itinerary"].expenses) == 2
    assert len(await store.list_versions(bid)) == 3
