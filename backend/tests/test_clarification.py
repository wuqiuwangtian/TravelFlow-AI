import json

import pytest
from conftest import FakeLLM
from test_itinerary_api import generate

from app.agents.checkpoint import build_serde, thread_config
from app.agents.nodes.refiner import RefineIntent
from app.domain.clarification import JourneyClarification
from app.domain.models import Requirements
from app.services.journey_clarification import resume_journey_message
from app.services.journey_messages import prepare_journey_message


async def setup(environment):
    client, store, graph, _ = environment
    sid, bid, _ = await generate(client)

    async def say(message, stream=False):
        response = await client.post(
            "/api/chat/stream" if stream else "/api/chat",
            json={"session_id": sid, "branch_id": bid, "message": message},
        )
        assert response.status_code == 200, response.text
        if stream:
            done = response.text.split("event: done\ndata: ")[-1].split("\n\n")[0]
            return json.loads(done)
        return response.json()

    await say("第1天午餐花了86元；第2天午餐花了90元")
    return client, store, graph, sid, bid, say


async def test_short_day_answer_resumes_edit_without_model_or_empty_versions(
    environment, monkeypatch
):
    client, store, graph, _, bid, say = await setup(environment)

    async def offline(*args, **kwargs):
        raise AssertionError("澄清明确记录不应调用模型")

    monkeypatch.setattr(FakeLLM, "structured", offline)
    before = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    pending = await say("午餐记错了，改成80元", stream=True)
    assert pending["clarification"]["kind"] == "day"
    assert len(await store.list_versions(bid)) == 2
    history = (await client.get(f"/api/branches/{bid}/messages")).json()
    assert history["clarification"] == pending["clarification"]
    snapshot = await graph.aget_state(thread_config(bid))
    assert snapshot.values["itinerary"] == before
    encoded = build_serde().dumps_typed(snapshot.values["pending_journey"])
    restored = build_serde().loads_typed(encoded)
    assert isinstance(restored, JourneyClarification)
    assert restored == snapshot.values["pending_journey"]
    result = await say("是第二天", stream=True)
    assert result["clarification"] is None
    current = (await graph.aget_state(thread_config(bid))).values
    assert [(e.day, e.amount_cents) for e in current["itinerary"].expenses] == [
        (1, 8600),
        (2, 8000),
    ]
    assert len(await store.list_versions(bid)) == 3
    assert current["pending_journey"] is None


async def test_batch_waits_as_a_whole_and_keeps_original_day_scopes(environment):
    _, store, graph, _, bid, say = await setup(environment)
    pending = await say("午餐记错了，改成80元；第1天第1站已完成；地铁花了5元")
    assert pending["clarification"]
    before = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert before.days[0].activities[0].progress == "not_started"
    await say("第二天")
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert current.days[0].activities[0].progress == "completed"
    assert current.days[1].activities[0].progress == "not_started"
    assert [(e.day, e.amount_cents) for e in current.expenses] == [(1, 8600), (2, 8000), (1, 500)]
    assert len(await store.list_versions(bid)) == 3


async def test_invalid_amount_can_be_corrected_and_whole_batch_runs_once(environment):
    _, store, graph, _, bid, say = await setup(environment)
    result = await say("第1天第1站已完成，咖啡花了1.001元")
    assert result["clarification"]["kind"] == "amount"
    assert (await say("0元"))["clarification"]["kind"] == "amount"
    assert len(await store.list_versions(bid)) == 2
    await say("20.50元")
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert len(current.expenses) == 3 and current.expenses[-1].amount_cents == 2050
    assert current.expenses[-1].day == 1
    assert current.days[0].activities[0].progress == "completed"
    assert len(await store.list_versions(bid)) == 3


async def test_activity_day_and_station_answers_and_refund_day(environment):
    _, _, graph, _, bid, say = await setup(environment)
    assert (await say("第2站已完成"))["clarification"]["kind"] == "day"
    await say("第二天")
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"].days[1].activities[
        1
    ].progress == "completed"
    assert (await say("第1天不存在的地点已完成"))["clarification"]["kind"] == "activity"
    await say("第1天第1站")
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"].days[0].activities[
        0
    ].progress == "completed"
    assert (await say("午餐退款到账20元"))["clarification"]["kind"] == "day"
    await say("第二天")
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert current.adjustments[0].expense_id == current.expenses[1].id


async def test_cancel_keeps_history_and_does_not_make_an_empty_version(environment):
    client, store, graph, _, bid, say = await setup(environment)
    await say("午餐记错了，改成80元")
    result = await say("取消这次操作")
    assert "已取消" in result["reply"] and result["clarification"] is None
    assert len(await store.list_versions(bid)) == 2
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"].expenses[
        1
    ].amount_cents == 9000
    assert (await client.get(f"/api/branches/{bid}/messages")).json()["clarification"] is None


async def test_clarifications_after_confirmation_do_not_make_empty_versions(environment):
    _, store, graph, _, bid, say = await setup(environment)
    await say("本次确认")
    assert len(await store.list_versions(bid)) == 3
    await say("午餐记错了，改成80元")
    await say("取消这次操作")
    assert len(await store.list_versions(bid)) == 3
    assert (await graph.aget_state(thread_config(bid))).values["pending_journey"] is None


@pytest.mark.parametrize("answer", ["第二天吗？", "第二天轻松一点", "如果是第二天", "明天", "确认"])
async def test_other_topics_never_resume_the_previous_operation(environment, monkeypatch, answer):
    _, _, graph, _, bid, say = await setup(environment)
    await say("午餐记错了，改成80元")

    async def unclear(*args, **kwargs):
        return RefineIntent(intent="unclear")

    monkeypatch.setattr(FakeLLM, "structured", unclear)
    result = await say(answer)
    assert result["clarification"] is None
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"].expenses[
        1
    ].amount_cents == 9000


async def test_fork_and_manual_edits_do_not_carry_pending_operations(environment):
    client, store, graph, sid, bid, say = await setup(environment)
    await say("午餐记错了，改成80元")
    fork = await client.post(
        f"/api/sessions/{sid}/branches", json={"title": "新分支", "parent_branch_id": bid}
    )
    new = next(b for b in fork.json()["items"] if b["title"] == "新分支")
    assert (await graph.aget_state(thread_config(new["id"]))).values["pending_journey"] is None
    assert (await graph.aget_state(thread_config(bid))).values["pending_journey"]
    latest = (await store.list_versions(bid))[-1]
    details = await store.get_version_detail(latest.id)
    expense = details.itinerary.expenses[0]
    updated = await client.put(
        f"/api/versions/{latest.id}/expenses/{expense.id}",
        json={"title": expense.title, "amount": 70, "category": "meal", "day": 1},
    )
    assert updated.status_code == 200, updated.text
    assert (await graph.aget_state(thread_config(bid))).values["pending_journey"] is None


async def test_changed_itinerary_invalidates_the_saved_operation(environment):
    _, _, graph, _, bid, _ = await setup(environment)
    itinerary = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    pending = prepare_journey_message(
        "午餐记错了，改成80元", itinerary, Requirements()
    ).clarification
    changed = itinerary.model_copy(deep=True)
    changed.expenses[1].amount_cents = 12000
    assert resume_journey_message("第二天", pending, changed, Requirements()) is None
