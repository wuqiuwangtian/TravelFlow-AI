import json
from datetime import datetime

import pytest
from conftest import FakeLLM
from test_activity_moves import seed
from test_journey_completion import close

from app.agents.checkpoint import thread_config
from app.api import routes_itinerary
from app.core.errors import LLMAuthenticationFailed
from app.domain.itinerary import BudgetItem, ExpenseAdjustment, RecapPhoto, RouteLeg, TravelRecap
from app.domain.workflow import DayOrder
from app.services.branch_lock import branch_guard


def reorder(inputs):
    result, group = [], []
    for activity in inputs["activities"]:
        if activity["fixed"]:
            result.extend(reversed(group))
            group = []
            result.append(activity["id"])
        else:
            group.append(activity["id"])
    return [*result, *reversed(group)]


@pytest.fixture
def model(monkeypatch):
    calls = []

    class LocalLLM(FakeLLM):
        async def structured(self, messages, schema, **kwargs):
            assert schema is DayOrder
            calls.append(messages)
            return DayOrder(activity_ids=reorder(json.loads(messages[-1].content)))

    monkeypatch.setattr(routes_itinerary, "create_llm_provider", lambda settings: LocalLLM())
    return calls


async def latest(store, bid):
    versions = await store.list_versions(bid)
    return await store.get_version_detail(versions[-1].id)


async def preview(client, vid, **overrides):
    return await client.post(
        f"/api/versions/{vid}/workflow/planner-preview",
        json={
            "request_id": "node-rerun-0001",
            "day": 2,
            "travel_mode": "walking",
            "instruction": "减少折返",
            **overrides,
        },
    )


async def apply(client, bid, rid="node-rerun-0001"):
    return await client.post(f"/api/branches/{bid}/workflow/planner-preview/{rid}/apply")


async def test_preview_is_durable_without_versions_or_checkpoint_writes_and_retry_reuses_model(
    environment,
    model,
):
    client, store, graph, search = environment
    _, bid, _ = await seed(environment)
    detail = await latest(store, bid)
    before = await graph.aget_state(thread_config(bid))
    versions = await store.list_versions(bid)
    result = await preview(client, detail.version.id)
    assert result.status_code == 200, result.text
    data = result.json()
    assert [a["id"] for a in data["after"]["activities"]] == ["museum", "park", "hotel-two"]
    assert data["protected_ids"] == ["hotel-two"]
    old_row = next(r for r in data["budget_before"]["rows"] if r["id"] == "stay-park")
    assert next(r for r in data["budget_after"]["rows"] if r["id"] == "stay-park") == old_row
    assert (await graph.aget_state(thread_config(bid))) == before
    assert await store.list_versions(bid) == versions and search.queries == []
    assert (await latest(store, bid)) == detail
    assert (await preview(client, detail.version.id)).json() == data
    assert len(model) == 1
    assert (await client.get(f"/api/branches/{bid}/workflow/planner-preview")).json() == data
    changed = await preview(client, detail.version.id, instruction="别的要求")
    assert changed.status_code == 409 and len(model) == 1
    assert (
        await client.delete(f"/api/branches/{bid}/workflow/planner-preview/node-rerun-0001")
    ).status_code == 204
    assert (await client.get(f"/api/branches/{bid}/workflow/planner-preview")).json() is None
    assert (await apply(client, bid)).status_code == 400


async def test_apply_preserves_all_actual_records_manual_costs_other_days_and_replays_exact_version(
    environment,
    model,
):
    client, store, graph, _ = environment
    _, bid, say = await seed(environment)
    detail = await latest(store, bid)
    plan = detail.itinerary
    assert plan is not None
    plan.map_status = "ready"
    plan.recap = TravelRecap(
        title="私人回忆",
        body="不发给模型的原文",
        source_fingerprint="old",
        photos=[RecapPhoto(id="b" * 32, caption="私人照片说明", included=True)],
    )
    plan.expenses[0].notes = "私人付款备注"
    plan.expenses[0].receipt_ids = ["a" * 32]
    plan.adjustments = [
        ExpenseAdjustment(
            id="refund",
            expense_id=plan.expenses[0].id,
            kind="refund",
            amount_cents=500,
            status="received",
            created_at=plan.expenses[0].created_at,
            updated_at=plan.expenses[0].updated_at,
        )
    ]
    await routes_itinerary._save_revision(store, graph, detail, "准备私密记录")
    detail = await latest(store, bid)
    before = detail.itinerary.model_copy(deep=True)
    result = await preview(client, detail.version.id)
    assert result.status_code == 200, result.text
    text = " ".join(message.content for message in model[0])
    assert all(
        private not in text
        for private in ("私人回忆", "不发给模型的原文", "私人付款备注", "私人照片说明", "a" * 32)
    )
    result = await apply(client, bid)
    assert result.status_code == 200, result.text
    applied = await latest(store, bid)
    assert applied.version.id == result.json()["version"]["id"]
    after = applied.itinerary
    assert after.map_status == "partial"
    assert after.days[0] == before.days[0] and after.days[2] == before.days[2]
    assert after.expenses == before.expenses and after.adjustments == before.adjustments
    assert after.budget_items == before.budget_items and after.recap == before.recap
    assert after.ended_at == before.ended_at
    park = next(a for a in after.days[1].activities if a.id == "park")
    old_park = before.days[1].activities[0]
    assert (
        park.cost == old_park.cost and park.cost_source == "manual" and park.place == old_park.place
    )
    assert after.days[1].travel_mode == "walking"
    snapshot = await graph.aget_state(thread_config(bid))
    assert snapshot.values["itinerary"] == after
    assert (await client.get(f"/api/branches/{bid}/workflow/planner-preview")).json() is None
    await say("第1天午餐花了12元")
    count = len(await store.list_versions(bid))
    replay = await apply(client, bid)
    assert replay.json() == result.json() and len(await store.list_versions(bid)) == count
    assert len((await latest(store, bid)).itinerary.expenses) == 2


@pytest.mark.parametrize(
    "ids",
    [
        ["park", "museum"],
        ["park", "park", "hotel-two"],
        ["other", "museum", "hotel-two"],
        ["hotel-two", "museum", "park"],
    ],
)
async def test_invalid_model_order_never_changes_records_or_saves_preview(
    environment, monkeypatch, ids
):
    client, store, graph, _ = environment
    _, bid, _ = await seed(environment)
    detail = await latest(store, bid)
    before = await graph.aget_state(thread_config(bid))

    class InvalidLLM:
        async def structured(self, *args):
            return DayOrder(activity_ids=ids)

    monkeypatch.setattr(routes_itinerary, "create_llm_provider", lambda settings: InvalidLLM())
    result = await preview(client, detail.version.id)
    assert result.status_code == 422 and "原方案未修改" in result.json()["error"]["message"]
    assert await store.get_workflow_preview(bid) is None
    assert (await graph.aget_state(thread_config(bid))) == before
    assert await latest(store, bid) == detail


async def test_executed_history_bookings_and_manual_times_are_fixed(environment, model):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment, booked=True)
    detail = await latest(store, bid)
    for number in (1, 2):
        result = await preview(
            client, detail.version.id, day=number, request_id=f"fixed-day-{number}"
        )
        assert result.status_code == 200, result.text
        data = result.json()
        for aid in data["protected_ids"]:
            assert next(a for a in data["after"]["activities"] if a["id"] == aid) == next(
                a for a in data["before"]["activities"] if a["id"] == aid
            )
    assert "museum" in data["protected_ids"]


async def test_stale_version_preview_cannot_overwrite_new_payment_and_history_is_readonly(
    environment, model
):
    client, store, _, _ = environment
    _, bid, say = await seed(environment)
    detail = await latest(store, bid)
    assert (await preview(client, detail.version.id)).status_code == 200
    await say("第1天午餐花了12元")
    newest = await latest(store, bid)
    assert (await apply(client, bid)).status_code == 409
    assert (
        await preview(client, detail.version.id, request_id="historical-preview")
    ).status_code == 409
    assert (await latest(store, bid)) == newest
    assert len(model) == 1


async def test_changed_checkpoint_and_pending_confirmation_block_preview_and_apply(
    environment, model
):
    client, store, graph, _ = environment
    _, bid, say = await seed(environment)
    detail = await latest(store, bid)
    assert (await preview(client, detail.version.id)).status_code == 200
    await say("第2站已完成")
    snapshot = await graph.aget_state(thread_config(bid))
    assert snapshot.values["pending_journey"]
    assert (await apply(client, bid)).status_code == 409
    assert (
        await preview(client, detail.version.id, request_id="pending-preview")
    ).status_code == 409
    assert (await graph.aget_state(thread_config(bid))) == snapshot and len(model) == 1
    assert (await client.get(f"/api/branches/{bid}/workflow/planner-preview")).json() is None


async def test_closed_trip_branch_lock_and_unknown_day_do_not_run_model(environment, model):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    detail = await latest(store, bid)
    async with branch_guard(bid):
        assert (await preview(client, detail.version.id)).status_code == 409
    assert (await preview(client, detail.version.id, day=60)).status_code == 400
    ended = await close(client, detail.version.id)
    assert (await preview(client, ended["version"]["id"])).status_code == 409
    assert model == []


async def test_model_failure_does_not_leave_partial_preview_or_change_checkpoint(
    environment, monkeypatch
):
    client, store, graph, _ = environment
    _, bid, _ = await seed(environment)
    detail = await latest(store, bid)
    snapshot = await graph.aget_state(thread_config(bid))

    class BrokenLLM:
        async def structured(self, *args):
            raise LLMAuthenticationFailed()

    monkeypatch.setattr(routes_itinerary, "create_llm_provider", lambda settings: BrokenLLM())
    result = await preview(client, detail.version.id)
    assert result.status_code == 502 and result.json()["error"]["code"] == "LLM_AUTH_FAILED"
    assert await store.get_workflow_preview(bid) is None
    assert (await graph.aget_state(thread_config(bid))) == snapshot


async def test_preview_cannot_be_applied_to_another_branch(environment, model):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    _, other, _ = await seed(environment)
    detail = await latest(store, bid)
    assert (await preview(client, detail.version.id)).status_code == 200
    before = await latest(store, other)
    assert (await apply(client, other)).status_code == 400
    assert await latest(store, other) == before


async def test_manual_transport_budget_keeps_original_endpoints_amount_and_ledger_link(
    environment, model
):
    client, store, graph, _ = environment
    _, bid, _ = await seed(environment)
    detail = await latest(store, bid)
    detail.itinerary.budget_items.append(
        BudgetItem(
            id="leg-museum",
            title="手动交通预算",
            planned_cents=900,
            category="transport",
            day=2,
            activity_id="museum",
            source="transport",
        )
    )
    detail.itinerary.days[1].legs = [
        RouteLeg(
            from_id="park",
            to_id="museum",
            mode="transit",
            status="planned",
            duration_seconds=1800,
            cost=9,
            cost_source="manual",
            cost_basis="total",
        )
    ]
    await routes_itinerary._save_revision(store, graph, detail, "手动交通预算样本")
    detail = await latest(store, bid)
    result = await preview(client, detail.version.id)
    assert result.status_code == 200, result.text
    data = result.json()
    assert [a["id"] for a in data["before"]["activities"]] == [
        a["id"] for a in data["after"]["activities"]
    ]
    row = next(row for row in data["budget_after"]["rows"] if row["id"] == "leg-museum")
    assert row["planned_cents"] == 900 and row["activity_id"] == "museum"
    leg = data["after"]["legs"][0]
    assert leg["from_id"] == "park" and leg["to_id"] == "museum"
    assert leg["cost"] == 9 and leg["cost_source"] == "manual"


async def test_unknown_transport_before_booking_is_a_conflict_and_cannot_be_applied(
    environment, model
):
    client, store, graph, _ = environment
    _, bid, _ = await seed(environment, booked=True)
    detail = await latest(store, bid)
    detail.itinerary.days[1].activities[1].transport_minutes = None
    await routes_itinerary._save_revision(store, graph, detail, "未知交通样本")
    detail = await latest(store, bid)
    result = await preview(client, detail.version.id)
    assert result.status_code == 200, result.text
    assert any("未核实交通" in conflict for conflict in result.json()["conflicts"])
    assert (await apply(client, bid)).status_code == 409
    assert await latest(store, bid) == detail


async def test_checkpoint_only_change_invalidates_preview_without_a_new_plan_version(
    environment, model
):
    client, store, graph, _ = environment
    _, bid, _ = await seed(environment)
    detail = await latest(store, bid)
    assert (await preview(client, detail.version.id)).status_code == 200
    await graph.aupdate_state(thread_config(bid), {"updated_at": datetime.now()}, as_node="refiner")
    result = await apply(client, bid)
    assert result.status_code == 409 and "对话已变化" in result.json()["error"]["message"]
    assert await latest(store, bid) == detail


async def test_an_applied_request_id_cannot_be_reused_for_a_new_preview(environment, model):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    detail = await latest(store, bid)
    assert (await preview(client, detail.version.id)).status_code == 200
    assert (await apply(client, bid)).status_code == 200
    newest = await latest(store, bid)
    await client.delete(f"/api/branches/{bid}/workflow/planner-preview/node-rerun-0001")
    result = await preview(client, newest.version.id)
    assert result.status_code == 409 and len(model) == 1
