from datetime import datetime

import pytest
from conftest import MARKDOWN, FakeLLM, FakeSearch, requirements
from move_fixtures import move_itinerary

from app.agents.checkpoint import open_checkpointer, thread_config
from app.agents.graph import build_graph
from app.agents.state import initial_state
from app.api import routes_itinerary
from app.domain.clarification import PlaceClarification
from app.domain.enums import PlanStatus
from app.domain.itinerary import RouteLeg, ScheduledActivity
from app.domain.models import ResearchBundle
from app.search.base import SearchResult
from app.services.chat_service import ChatService
from app.services.itinerary_service import sync_markdown
from app.services.plan_undo import UndoPlanInput, undo_plan
from app.storage.database import create_connection
from app.storage.session_store import SessionStore


async def latest(store, branch_id):
    return await store.get_version_detail((await store.list_versions(branch_id))[-1].id)


async def seed(environment, *, with_transport=False):
    client, store, graph, _ = environment
    session = await store.create_session("规划撤销验收")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    plan = move_itinerary(with_receipt=True)
    if with_transport:
        plan.days[1].legs = [
            RouteLeg(
                from_id="park",
                to_id="museum",
                mode="transit",
                status="planned",
                duration_seconds=1800,
                cost=10,
                cost_basis="total",
                cost_source="estimate",
            )
        ]
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


@pytest.mark.parametrize("item_id", ["stay-museum", "leg-museum"])
@pytest.mark.parametrize("amount", ["125.50", "0", None])
async def test_manual_budget_after_revision_still_allows_undo_and_preserves_latest_override(
    environment, item_id, amount
):
    client, store, _, _ = environment
    _, branch, before, changed = await seed(environment, with_transport=item_id.startswith("leg-"))
    response = await client.put(
        f"/api/versions/{changed.version.id}/budget-items/{item_id}",
        json={"amount": amount},
    )
    assert response.status_code == 200, response.text
    current = await latest(store, branch.id)
    change = await offer(client, current.version.id)
    assert change and change["version_id"] == changed.version.id
    response = await undo(client, current.version.id, changed.version.id)
    assert response.status_code == 200, response.text
    result = await latest(store, branch.id)
    assert result.itinerary.budget_items == current.itinerary.budget_items
    assert result.itinerary.expenses == current.itinerary.expenses
    assert (
        result.itinerary.days[1].activities[1].title == before.itinerary.days[1].activities[1].title
    )
    view = response.json()["budget_overview"]
    assert next(row for row in view["rows"] if row["id"] == item_id)["planned_cents"] == (
        int(float(amount) * 100) if amount is not None else None
    )


@pytest.mark.parametrize("operation", ["add", "edit", "delete"])
async def test_undo_keeps_latest_custom_budget_and_never_restores_deleted_rows(
    environment, operation
):
    client, store, _, _ = environment
    _, branch, _, changed = await seed(environment)
    created = await client.post(
        f"/api/versions/{changed.version.id}/budget-items",
        json={
            "title": "人工保险预估",
            "amount": "88.88",
            "category": "other",
            "request_id": "custom-undo-budget",
        },
    )
    assert created.status_code == 200
    current_id = created.json()["version"]["id"]
    endpoint = f"/api/versions/{current_id}/budget-items/custom-custom-undo-budget"
    if operation == "edit":
        assert (await client.put(endpoint, json={"amount": None})).status_code == 200
    elif operation == "delete":
        assert (await client.delete(endpoint)).status_code == 200
    current = await latest(store, branch.id)
    assert (await offer(client, current.version.id))["version_id"] == changed.version.id
    response = await undo(client, current.version.id, changed.version.id)
    assert response.status_code == 200, response.text
    result = await latest(store, branch.id)
    assert result.itinerary.budget_items == current.itinerary.budget_items
    assert result.itinerary.expenses == current.itinerary.expenses


async def add_planned_stop(store, graph, branch, detail, *, new_day=False):
    revised = detail.model_copy(deep=True)
    target = ScheduledActivity(
        id="new-undo-stop",
        title="新增餐厅",
        place_name="新增餐厅",
        city="北京",
        kind="meal",
        cost=50,
        cost_basis="total",
    )
    if new_day:
        from app.domain.itinerary import DayPlan

        revised.itinerary.days.append(DayPlan(day=4, activities=[target]))
        revised.requirements.duration_days = 4
    else:
        revised.itinerary.days[1].activities.append(target)
    revised.markdown = sync_markdown(detail.markdown, revised.itinerary, revised.requirements)
    values = dict((await graph.aget_state(thread_config(branch.id))).values)
    values.update(
        itinerary=revised.itinerary,
        requirements=revised.requirements,
        plan_markdown=revised.markdown,
    )
    config = await graph.aupdate_state(thread_config(branch.id), values, as_node="refiner")
    await ChatService(graph=graph, store=store).record_version(branch.id, values, config)
    return await latest(store, branch.id)


@pytest.mark.parametrize("finance", ["expense", "budget", "none"])
async def test_new_stop_with_financial_link_blocks_undo_without_leaving_dangling_links(
    environment, finance
):
    client, store, graph, _ = environment
    _, branch, _, changed = await seed(environment)
    added = await add_planned_stop(store, graph, branch, changed)
    if finance == "expense":
        response = await client.post(
            f"/api/versions/{added.version.id}/expenses",
            json={
                "title": "餐厅订金",
                "amount": "12.34",
                "category": "meal",
                "day": 2,
                "activity_id": "new-undo-stop",
                "budget_item_id": "stay-new-undo-stop",
                "request_id": "new-stop-expense",
            },
        )
        assert response.status_code == 200
    elif finance == "budget":
        assert (
            await client.put(
                f"/api/versions/{added.version.id}/budget-items/stay-new-undo-stop",
                json={"amount": "66.66"},
            )
        ).status_code == 200
    current = await latest(store, branch.id)
    change = await offer(client, current.version.id)
    response = await undo(client, current.version.id, added.version.id)
    if finance == "none":
        assert response.status_code == 200, response.text
        assert "new-undo-stop" not in {
            a["id"] for day in response.json()["itinerary"]["days"] for a in day["activities"]
        }
    else:
        assert "关联" in change["blocked_reason"] and response.status_code == 409
        assert await latest(store, branch.id) == current


@pytest.mark.parametrize("finance", ["expense", "custom-budget"])
async def test_new_day_with_financial_records_blocks_undo_without_rewriting_payment_day(
    environment, finance
):
    client, store, graph, _ = environment
    _, branch, _, changed = await seed(environment)
    added = await add_planned_stop(store, graph, branch, changed, new_day=True)
    if finance == "expense":
        response = await client.post(
            f"/api/versions/{added.version.id}/expenses",
            json={
                "title": "第四天车费",
                "amount": "9.87",
                "day": 4,
                "category": "transport",
                "request_id": "new-day-paid",
            },
        )
    else:
        response = await client.post(
            f"/api/versions/{added.version.id}/budget-items",
            json={"title": "第四天预留", "amount": None, "day": 4, "request_id": "new-day-budget"},
        )
    assert response.status_code == 200
    current = await latest(store, branch.id)
    assert "日期" in (await offer(client, current.version.id))["blocked_reason"]
    assert (await undo(client, current.version.id, added.version.id)).status_code == 409
    assert await latest(store, branch.id) == current


async def test_budget_payment_refund_and_saved_recap_survive_undo_and_historical_exports(
    environment, monkeypatch
):
    client, store, graph, _ = environment
    _, branch, before, changed = await seed(environment)

    async def no_model(*args, **kwargs):
        raise AssertionError("撤销、预算、记账、回顾和导出不应调用模型")

    monkeypatch.setattr(FakeLLM, "structured", no_model)
    response = await client.put(
        f"/api/versions/{changed.version.id}/budget-items/stay-hotel-one", json={"amount": "125.50"}
    )
    assert response.status_code == 200
    vid = response.json()["version"]["id"]
    response = await client.post(
        f"/api/versions/{vid}/expenses",
        json={
            "title": "实际住宿付款",
            "amount": "86.35",
            "category": "lodging",
            "day": 2,
            "activity_id": "hotel-one",
            "budget_item_id": "stay-hotel-one",
            "request_id": "undo-hotel-payment",
        },
    )
    assert response.status_code == 200
    vid = response.json()["version"]["id"]
    response = await client.post(
        f"/api/versions/{vid}/expense-adjustments",
        json={
            "kind": "refund",
            "status": "received",
            "expense_id": "undo-hotel-payment",
            "amount": "5",
            "request_id": "undo-hotel-refund",
        },
    )
    assert response.status_code == 200
    vid = response.json()["version"]["id"]
    recap = (await client.get(f"/api/versions/{vid}/recap")).json()
    response = await client.put(
        f"/api/versions/{vid}/recap",
        json={
            "title": "手写回忆",
            "body": "原文应保留",
            "source_fingerprint": recap["source_fingerprint"],
        },
    )
    assert response.status_code == 200
    current = await latest(store, branch.id)
    assert (await offer(client, current.version.id))["version_id"] == changed.version.id
    undone = await undo(client, current.version.id, changed.version.id)
    assert undone.status_code == 200, undone.text
    result = await latest(store, branch.id)
    assert result.itinerary.expenses == current.itinerary.expenses
    assert result.itinerary.adjustments == current.itinerary.adjustments
    assert result.itinerary.budget_items == current.itinerary.budget_items
    assert result.itinerary.recap == current.itinerary.recap
    view = (await client.get(f"/api/versions/{result.version.id}/recap")).json()
    assert view["ledger"] == recap["ledger"]
    assert view["ledger"]["paid_cents"] == 10635 and view["ledger"]["net_expense_cents"] == 10135
    assert view["body"] == "原文应保留" and view["stale"]
    count = len(await store.list_versions(branch.id))
    for fmt in ("md", "html"):
        for version in (before, current, result):
            exported = await client.get(f"/api/versions/{version.version.id}/export?format={fmt}")
            assert exported.status_code == 200
        exported = await client.get(f"/api/versions/{result.version.id}/recap/export?format={fmt}")
        assert exported.status_code == 200
        assert (
            "106.35" in exported.text
            and "101.35" in exported.text
            and "125.50" not in exported.text
        )
    assert await store.get_version_detail(before.version.id) == before
    assert await store.get_version_detail(current.version.id) == current
    assert len(await store.list_versions(branch.id)) == count
    assert (await graph.aget_state(thread_config(branch.id))).values[
        "itinerary"
    ] == result.itinerary


async def test_undo_after_later_refinement_does_not_restore_an_earlier_cancelled_train(environment):
    client, store, graph, _ = environment
    _, branch, _, changed = await seed(environment)

    async def record(detail):
        detail.markdown = sync_markdown(detail.markdown, detail.itinerary, detail.requirements)
        values = dict((await graph.aget_state(thread_config(branch.id))).values)
        values.update(itinerary=detail.itinerary, plan_markdown=detail.markdown)
        config = await graph.aupdate_state(thread_config(branch.id), values, as_node="refiner")
        await ChatService(graph=graph, store=store).record_version(branch.id, values, config)
        return await latest(store, branch.id)

    added = changed.model_copy(deep=True)
    added.itinerary.days[1].activities.append(
        ScheduledActivity(
            id="cancelled-train",
            title="已明确取消的返程高铁",
            place_name="上海虹桥站",
            city="上海",
            kind="station",
            transport_mode="rail",
        )
    )
    added = await record(added)
    cancelled = added.model_copy(deep=True)
    cancelled.itinerary.days[1].activities.pop()
    cancelled = await record(cancelled)
    refined = cancelled.model_copy(deep=True)
    refined.itinerary.days[1].title = "取消返程后的轻松安排"
    refined = await record(refined)
    response = await client.put(
        f"/api/versions/{refined.version.id}/budget-items/stay-hotel-one", json={"amount": "125.50"}
    )
    assert response.status_code == 200
    current = await latest(store, branch.id)
    change = await offer(client, current.version.id)
    assert (
        change["version_id"] == refined.version.id
        and change["previous_version_id"] == cancelled.version.id
    )
    response = await undo(client, current.version.id, refined.version.id)
    assert response.status_code == 200, response.text
    result = await latest(store, branch.id)
    assert "cancelled-train" not in {a.id for day in result.itinerary.days for a in day.activities}
    assert result.itinerary.budget_items == current.itinerary.budget_items
    assert await store.get_version_detail(added.version.id) == added


async def test_latest_budget_follows_paid_stop_back_to_original_day_on_undo(environment):
    client, store, graph, _ = environment
    _, branch, _, changed = await seed(environment)
    moved = changed.model_copy(deep=True)
    park = moved.itinerary.days[1].activities.pop(0)
    moved.itinerary.days[0].activities.append(park)
    moved.markdown = sync_markdown(moved.markdown, moved.itinerary, moved.requirements)
    values = dict((await graph.aget_state(thread_config(branch.id))).values)
    values.update(itinerary=moved.itinerary, plan_markdown=moved.markdown)
    config = await graph.aupdate_state(thread_config(branch.id), values, as_node="refiner")
    await ChatService(graph=graph, store=store).record_version(branch.id, values, config)
    moved = await latest(store, branch.id)
    response = await client.put(
        f"/api/versions/{moved.version.id}/budget-items/stay-park", json={"amount": "125.50"}
    )
    assert response.status_code == 200
    current = await latest(store, branch.id)
    response = await undo(client, current.version.id, moved.version.id)
    assert response.status_code == 200, response.text
    row = next(
        row for row in response.json()["budget_overview"]["rows"] if row["id"] == "stay-park"
    )
    assert row["planned_cents"] == 12550 and row["day"] == 2
    assert (await latest(store, branch.id)).itinerary.expenses == current.itinerary.expenses


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


@pytest.mark.parametrize("operation", ["organise", "fork"])
async def test_partial_undo_version_cannot_replace_plan_with_empty_snapshot(
    environment, monkeypatch, operation
):
    client, store, graph, _ = environment
    session, branch, before, changed = await seed(environment)
    save_plan = store.save_plan

    async def fail(*args, **kwargs):
        raise RuntimeError("模拟版本正文写入中断")

    monkeypatch.setattr(store, "save_plan", fail)
    with pytest.raises(RuntimeError, match="模拟版本正文写入中断"):
        await undo(client, changed.version.id, changed.version.id)
    monkeypatch.setattr(store, "save_plan", save_plan)
    partial = await latest(store, branch.id)
    assert partial.markdown == "" and partial.itinerary is None
    if operation == "organise":
        monkeypatch.setattr(routes_itinerary, "create_llm_provider", lambda _: FakeLLM())
        response = await client.post(f"/api/versions/{partial.version.id}/itinerary")
        assert response.status_code == 409, response.text
        assert "撤销结果已恢复" in response.text
    else:
        response = await client.post(
            f"/api/sessions/{session.id}/branches",
            json={
                "title": "恢复后分叉",
                "parent_branch_id": branch.id,
                "fork_version_id": partial.version.id,
            },
        )
        assert response.status_code == 200, response.text
        fork = next(b for b in response.json()["items"] if b["title"] == "恢复后分叉")
        assert (await latest(store, fork["id"])).itinerary == before.itinerary
    restored = await latest(store, branch.id)
    assert restored.version.id == partial.version.id
    assert restored.itinerary == before.itinerary
    assert len(await store.list_versions(branch.id)) == 3
    assert (await graph.aget_state(thread_config(branch.id))).values[
        "itinerary"
    ] == before.itinerary
