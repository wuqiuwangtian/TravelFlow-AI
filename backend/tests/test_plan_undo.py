from datetime import datetime

import pytest
from conftest import MARKDOWN, FakeLLM, FakeSearch, requirements
from move_fixtures import move_itinerary

from app.agents.checkpoint import open_checkpointer, thread_config
from app.agents.graph import build_graph
from app.agents.state import initial_state
from app.domain.clarification import PlaceClarification
from app.domain.enums import PlanStatus
from app.domain.itinerary import ScheduledActivity
from app.domain.models import ResearchBundle
from app.search.base import SearchResult
from app.services.chat_service import ChatService
from app.services.itinerary_service import sync_markdown
from app.services.plan_undo import UndoPlanInput, undo_plan
from app.storage.database import create_connection
from app.storage.session_store import SessionStore


async def latest(store, branch_id):
    return await store.get_version_detail((await store.list_versions(branch_id))[-1].id)


async def seed(environment):
    client, store, graph, _ = environment
    session = await store.create_session("规划撤销验收")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    plan = move_itinerary(with_receipt=True)
    req = requirements()
    values = {
        **initial_state(),
        "requirements": req,
        "itinerary": plan,
        "plan_markdown": sync_markdown(MARKDOWN, plan, req),
        "status": PlanStatus.REVIEWING,
        "research": ResearchBundle(
            transport=[
                SearchResult(
                    title="原资料",
                    url="https://example.com/original",
                    content="原始事实",
                    provider="fake",
                )
            ]
        ),
    }
    config = await graph.aupdate_state(thread_config(branch.id), values, as_node="planner")
    service = ChatService(graph=graph, store=store)
    await service.record_version(branch.id, values, config)
    before = await latest(store, branch.id)
    after = before.model_copy(deep=True)
    after.itinerary.days[1].title = "下午轻松漫步"
    after.itinerary.days[1].activities[1].title = "喝茶休息"
    after.itinerary.days[1].activities[1].cost = 38
    after.markdown = sync_markdown(after.markdown, after.itinerary, req)
    patch = {
        "itinerary": after.itinerary,
        "plan_markdown": after.markdown,
        "revision": 1,
    }
    config = await graph.aupdate_state(thread_config(branch.id), patch, as_node="refiner")
    await service.record_version(branch.id, {**values, **patch}, config)
    return session, branch, before, await latest(store, branch.id)


async def offer(client, version_id):
    response = await client.get(f"/api/versions/{version_id}/planning-change")
    assert response.status_code == 200, response.text
    return response.json()


async def undo(client, current_id, change_id, request_id="undo-request-0001"):
    return await client.post(
        f"/api/versions/{current_id}/undo-plan",
        json={
            "request_id": request_id,
            "change_version_id": change_id,
        },
    )


async def test_undo_adds_version_restores_route_sources_and_checkpoint_without_model(environment):
    client, store, graph, search = environment
    _, branch, before, changed = await seed(environment)
    change = await offer(client, changed.version.id)
    assert change["previous_version_id"] == before.version.id
    assert "第 2 天安排已更新" in change["changes"]
    assert change["blocked_reason"] is None
    queries = search.queries[:]
    response = await undo(client, changed.version.id, changed.version.id)
    assert response.status_code == 200, response.text
    result = await latest(store, branch.id)
    assert result.version.seq == 3 and result.version.trigger.startswith("plan_undo:")
    assert result.itinerary == before.itinerary
    assert result.citations == before.citations
    assert result.markdown.strip() == before.markdown.strip()
    assert (await store.get_version_detail(changed.version.id)) == changed
    snapshot = await graph.aget_state(thread_config(branch.id))
    assert snapshot.values["itinerary"] == result.itinerary
    assert snapshot.values["plan_markdown"] == result.markdown
    assert snapshot.values["research"].transport[0].content == "原始事实"
    assert snapshot.config["configurable"]["checkpoint_id"] == result.version.checkpoint_id
    assert search.queries == queries
    assert await offer(client, result.version.id) is None


async def test_undo_after_expense_and_refund_keeps_exact_records_and_receipt_links(environment):
    client, store, graph, _ = environment
    _, branch, before, changed = await seed(environment)
    response = await client.post(
        f"/api/versions/{changed.version.id}/expenses",
        json={
            "request_id": "paid-tea-001",
            "title": "下午喝茶",
            "amount": "28.50",
            "day": 2,
            "category": "meal",
        },
    )
    assert response.status_code == 200, response.text
    paid = response.json()
    response = await client.post(
        f"/api/versions/{paid['version']['id']}/expense-adjustments",
        json={
            "request_id": "refund-tea-001",
            "expense_id": "paid-tea-001",
            "kind": "refund",
            "amount": "5.00",
            "status": "received",
        },
    )
    assert response.status_code == 200, response.text
    current = await latest(store, branch.id)
    assert (await offer(client, current.version.id))["version_id"] == changed.version.id
    response = await undo(client, current.version.id, changed.version.id)
    assert response.status_code == 200, response.text
    result = await latest(store, branch.id)
    assert result.itinerary.days == before.itinerary.days
    assert result.itinerary.expenses == current.itinerary.expenses
    assert result.itinerary.adjustments == current.itinerary.adjustments
    assert result.itinerary.expenses[0].receipt_ids
    assert result.itinerary.ledger_summary == current.itinerary.ledger_summary
    assert "28.50" in result.markdown and "5.00" in result.markdown
    assert (await graph.aget_state(thread_config(branch.id))).values[
        "itinerary"
    ] == result.itinerary


async def test_progress_on_retained_stop_survives_undo(environment):
    client, store, _, _ = environment
    _, branch, _, changed = await seed(environment)
    response = await client.patch(
        f"/api/versions/{changed.version.id}/days/1/activities/station/progress",
        json={"progress": "completed"},
    )
    assert response.status_code == 200, response.text
    current = await latest(store, branch.id)
    response = await undo(client, current.version.id, changed.version.id)
    assert response.status_code == 200, response.text
    result = await latest(store, branch.id)
    assert result.itinerary.days[0].activities == current.itinerary.days[0].activities
    assert result.itinerary.expenses == current.itinerary.expenses


async def test_stale_version_and_later_plan_edit_never_overwrite_current_plan(environment):
    client, store, graph, _ = environment
    _, branch, _, changed = await seed(environment)
    activity = changed.itinerary.days[1].activities[0]
    response = await client.put(
        f"/api/versions/{changed.version.id}/days/2/activities/{activity.id}",
        json={"activity": {**activity.model_dump(), "title": "手动新安排"}},
    )
    assert response.status_code == 200, response.text
    current = await latest(store, branch.id)
    snapshot = await graph.aget_state(thread_config(branch.id))
    assert await offer(client, current.version.id) is None
    for current_id in (changed.version.id, current.version.id):
        response = await undo(client, current_id, changed.version.id)
        assert response.status_code == 409
    assert await latest(store, branch.id) == current
    assert (await graph.aget_state(thread_config(branch.id))).config == snapshot.config


async def test_replay_same_request_after_later_expense_returns_receipt_without_new_write(
    environment,
):
    client, store, graph, _ = environment
    _, branch, _, changed = await seed(environment)
    first = await undo(client, changed.version.id, changed.version.id)
    assert first.status_code == 200
    saved = first.json()
    response = await client.post(
        f"/api/versions/{saved['version']['id']}/expenses",
        json={
            "request_id": "later-payment-1",
            "title": "晚餐",
            "amount": "66.00",
            "category": "meal",
        },
    )
    assert response.status_code == 200, response.text
    current = await latest(store, branch.id)
    snapshot = await graph.aget_state(thread_config(branch.id))
    replay = await undo(client, changed.version.id, changed.version.id)
    assert replay.json() == saved
    assert await latest(store, branch.id) == current
    assert (await graph.aget_state(thread_config(branch.id))).config == snapshot.config
    assert (await undo(client, changed.version.id, "different-change")).status_code == 409
    assert (
        await undo(client, current.version.id, changed.version.id, "another-request")
    ).status_code == 409


@pytest.mark.parametrize("failure", ["create_version", "save_plan"])
async def test_retry_recovers_checkpoint_and_partial_version_without_repeated_undo(
    environment, monkeypatch, failure
):
    client, store, graph, _ = environment
    _, branch, before, changed = await seed(environment)
    original = getattr(store, failure)

    async def fail(*args, **kwargs):
        raise RuntimeError("模拟存储中断")

    monkeypatch.setattr(store, failure, fail)
    with pytest.raises(RuntimeError, match="模拟存储中断"):
        await undo(client, changed.version.id, changed.version.id)
    snapshot = await graph.aget_state(thread_config(branch.id))
    monkeypatch.setattr(store, failure, original)
    response = await undo(client, changed.version.id, changed.version.id)
    assert response.status_code == 200, response.text
    assert len(await store.list_versions(branch.id)) == 3
    assert (await latest(store, branch.id)).itinerary == before.itinerary
    assert (await graph.aget_state(thread_config(branch.id))).config == snapshot.config


async def test_next_expense_does_not_clobber_interrupted_undo(environment, monkeypatch):
    client, store, graph, _ = environment
    _, branch, before, changed = await seed(environment)
    original = store.create_version

    async def fail(*args, **kwargs):
        raise RuntimeError("模拟退出")

    monkeypatch.setattr(store, "create_version", fail)
    with pytest.raises(RuntimeError):
        await undo(client, changed.version.id, changed.version.id)
    monkeypatch.setattr(store, "create_version", original)
    response = await client.post(
        f"/api/versions/{changed.version.id}/expenses",
        json={
            "request_id": "new-expense-1",
            "title": "晚餐",
            "amount": "12.00",
        },
    )
    assert response.status_code == 409, response.text
    current = await latest(store, branch.id)
    assert current.itinerary == before.itinerary
    assert (await graph.aget_state(thread_config(branch.id))).values[
        "itinerary"
    ] == current.itinerary


async def test_initial_generation_history_and_cross_branch_not_offered(environment):
    client, store, _, _ = environment
    _, _, before, changed = await seed(environment)
    assert await offer(client, before.version.id) is None
    _, other_branch, other_before, other_changed = await seed(environment)
    assert (await undo(client, other_changed.version.id, changed.version.id)).status_code == 409
    assert await latest(store, other_branch.id) == other_changed
    assert before.version.branch_id != other_before.version.branch_id


async def test_new_stop_with_progress_blocks_undo_without_dropping_it(environment):
    client, store, graph, _ = environment
    _, branch, _, changed = await seed(environment)
    changed.itinerary.days[1].activities.append(
        ScheduledActivity(
            id="new-tea", title="新茶馆", progress="in_progress", progress_updated_at=datetime.now()
        )
    )
    markdown = sync_markdown(changed.markdown, changed.itinerary, changed.requirements)
    patch = {"itinerary": changed.itinerary, "plan_markdown": markdown}
    config = await graph.aupdate_state(thread_config(branch.id), patch, as_node="refiner")
    values = (await graph.aget_state(config)).values
    await ChatService(graph=graph, store=store).record_version(branch.id, values, config)
    current = await latest(store, branch.id)
    change = await offer(client, current.version.id)
    assert "已有游玩进度或预约" in change["blocked_reason"]
    assert (await undo(client, current.version.id, current.version.id)).status_code == 409
    assert await latest(store, branch.id) == current


async def test_undo_keeps_explicit_reset_to_not_started(environment):
    client, store, _, _ = environment
    _, branch, _, changed = await seed(environment)
    response = await client.patch(
        f"/api/versions/{changed.version.id}/days/1/activities/station/progress",
        json={"progress": "not_started"},
    )
    assert response.status_code == 200
    current = await latest(store, branch.id)
    response = await undo(client, current.version.id, changed.version.id)
    assert response.status_code == 200, response.text
    assert (await latest(store, branch.id)).itinerary.days[0].activities == current.itinerary.days[
        0
    ].activities


async def test_pending_confirmation_and_unindexed_checkpoint_block_undo(environment):
    client, store, graph, _ = environment
    _, branch, _, changed = await seed(environment)
    await graph.aupdate_state(
        thread_config(branch.id),
        {
            "pending_place": PlaceClarification(
                prompt="请选择地点", itinerary_fingerprint="fixture"
            ),
        },
        as_node="refiner",
    )
    response = await undo(client, changed.version.id, changed.version.id)
    assert response.status_code == 409 and "待确认" in response.text
    await graph.aupdate_state(
        thread_config(branch.id),
        {
            "pending_place": None,
            "plan_markdown": "尚未索引的新计划",
        },
        as_node="refiner",
    )
    response = await undo(client, changed.version.id, changed.version.id)
    assert response.status_code == 409 and "对话或方案已变化" in response.text
    assert await latest(store, branch.id) == changed


async def test_actual_chat_revision_can_be_undone_and_next_message_uses_restored_state(environment):
    client, store, graph, _ = environment
    session, branch, _, before = await seed(environment)
    response = await client.post(
        "/api/chat",
        json={
            "session_id": session.id,
            "branch_id": branch.id,
            "message": "第2天轻松一点",
            "request_id": "actual-chat-revision",
        },
    )
    assert response.status_code == 200, response.text
    changed = await latest(store, branch.id)
    assert (await offer(client, changed.version.id))["blocked_reason"] is None
    response = await undo(client, changed.version.id, changed.version.id)
    assert response.status_code == 200, response.text
    assert (await latest(store, branch.id)).itinerary.days == before.itinerary.days
    response = await client.post(
        "/api/chat",
        json={
            "session_id": session.id,
            "branch_id": branch.id,
            "message": "第2天记一笔晚餐消费29.50元",
            "request_id": "actual-chat-after-undo",
        },
    )
    assert response.status_code == 200, response.text
    result = await latest(store, branch.id)
    assert result.itinerary.days == before.itinerary.days
    assert any(e.amount_cents == 2950 for e in result.itinerary.expenses)
    assert (await graph.aget_state(thread_config(branch.id))).values[
        "itinerary"
    ] == result.itinerary


async def test_recover_undo_after_real_sqlite_checkpoint_reopen(tmp_path, monkeypatch):
    path = tmp_path / "restart.db"
    conn = await create_connection(path)
    store = SessionStore(conn)
    payload = UndoPlanInput(request_id="restart-undo-request", change_version_id="placeholder")
    async with open_checkpointer(path) as saver:
        graph = build_graph(FakeLLM(), FakeSearch(), checkpointer=saver)
        _, branch, before, changed = await seed((None, store, graph, None))
        payload.change_version_id = changed.version.id
        original = store.create_version

        async def fail(*args, **kwargs):
            raise RuntimeError("模拟进程退出")

        monkeypatch.setattr(store, "create_version", fail)
        with pytest.raises(RuntimeError, match="模拟进程退出"):
            await undo_plan(store, graph, changed.version.id, payload)
        checkpoint = (await graph.aget_state(thread_config(branch.id))).config
        monkeypatch.setattr(store, "create_version", original)
    await conn.close()
    conn = await create_connection(path)
    try:
        store = SessionStore(conn)
        async with open_checkpointer(path) as saver:
            graph = build_graph(FakeLLM(), FakeSearch(), checkpointer=saver)
            result = await undo_plan(store, graph, changed.version.id, payload)
            assert result.itinerary == before.itinerary
            assert result.version.seq == 3
            assert (await graph.aget_state(thread_config(branch.id))).config == checkpoint
            replay = await undo_plan(store, graph, changed.version.id, payload)
            assert replay == result
            assert len(await store.list_versions(branch.id)) == 3
    finally:
        await conn.close()
