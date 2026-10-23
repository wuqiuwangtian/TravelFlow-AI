import pytest
from conftest import MARKDOWN, FakeLLM
from move_fixtures import move_itinerary

from app.agents.checkpoint import build_serde, thread_config
from app.agents.state import initial_state
from app.core.config import Settings
from app.core.errors import InvalidRequest
from app.domain.clarification import clarification_prompt
from app.domain.enums import PlanStatus
from app.domain.itinerary import ExpenseInput, ScheduledActivity
from app.domain.linked_replan import ActivityMove, LinkedReplanInput
from app.domain.models import Requirements
from app.services.activity_move_messages import handle_move_message
from app.services.activity_move_service import apply_activity_moves
from app.services.adjustment_messages import handle_adjustment_message
from app.services.budget_service import budget_overview
from app.services.chat_service import ChatService
from app.services.expense_dates import planned_expense_day
from app.services.itinerary_service import sync_markdown
from app.services.linked_replan_service import preview_linked_replan
from app.services.travel_service import edit_expense

SETTINGS = Settings(_env_file=None, amap_api_key=None)
REQ = Requirements(destination="北京", duration_days=3, budget=2000, budget_basis="total")


def move(activity="park", source=2, destination=1, before=None):
    return LinkedReplanInput(
        first_day=min(source, destination),
        moves=[
            ActivityMove(
                activity_id=activity,
                source_day=source,
                destination_day=destination,
                before_activity_id=before,
            )
        ],
    )


async def seed(environment, *, ambiguous=False, booked=False):
    client, store, graph, _ = environment
    session = await store.create_session("景点搬移验收")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    plan = move_itinerary(ambiguous=ambiguous, booked=booked)
    values = {
        **initial_state(),
        "requirements": REQ,
        "itinerary": plan,
        "plan_markdown": sync_markdown(MARKDOWN.replace("[1]", ""), plan, REQ),
        "status": PlanStatus.REVIEWING,
    }
    config = await graph.aupdate_state(thread_config(branch.id), values, as_node="planner")
    await ChatService(graph=graph, store=store).record_version(branch.id, values, config)

    async def say(message, request_id=None):
        response = await client.post(
            "/api/chat",
            json={
                "session_id": session.id,
                "branch_id": branch.id,
                "message": message,
                "request_id": request_id,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    return session.id, branch.id, say


async def test_paid_activity_moves_identity_budget_and_export_but_keeps_payment_and_receipt():
    original = move_itinerary(with_receipt=True)
    refund = handle_adjustment_message("第2天预付公园门票退款到账5元", original)
    assert refund and refund.itinerary
    original = refund.itinerary
    before = original.model_dump_json()
    activity = original.days[1].activities[0]
    preview = await preview_linked_replan(original, move(), REQ, SETTINGS)
    assert not preview.conflicts
    result = preview.itinerary
    moved = next(a for a in result.days[0].activities if a.id == "park")
    assert moved.id == activity.id and moved.cost == activity.cost
    assert moved.place == activity.place and moved.candidates == activity.candidates
    assert moved.notes == activity.notes and moved.duration_minutes == activity.duration_minutes
    assert not any(a.id == "park" for a in result.days[1].activities)
    assert result.expenses == original.expenses and result.adjustments == original.adjustments
    assert result.expenses[0].day == 2 and result.expenses[0].receipt_ids
    assert planned_expense_day(result, result.expenses[0]) == 1
    assert result.budget_items[0].day == 1 and result.budget_items[0].id == "stay-park"
    row = next(r for r in budget_overview(result, REQ).rows if r.id == "stay-park")
    assert row.day == 1 and row.actual_cents == 1500 and row.planned_cents == 2000
    assert result.days[2] == original.days[2]
    assert result.days[0].activities[0] == original.days[0].activities[0]
    assert all(a.progress == "not_started" for a in result.days[1].activities)
    assert {c.status for c in preview.changes if c.title == activity.title} == {
        "moved_in",
        "moved_out",
    }
    assert "跨天搬移" in preview.prompt
    assert original.model_dump_json() == before
    output = sync_markdown(MARKDOWN, result, REQ)
    assert "付款日：第 2 天" in output and "关联行程：第 1 天" in output
    entry = result.expenses[0]
    edit_expense(
        result,
        entry.id,
        ExpenseInput(
            title=entry.title,
            amount="18",
            day=2,
            category=entry.category,
            activity_id=entry.activity_id,
            budget_item_id=entry.budget_item_id,
            receipt_ids=entry.receipt_ids,
            settles_budget=True,
        ),
    )
    assert result.expenses[0].day == 2 and result.expenses[0].amount_cents == 1800
    assert result.expenses[0].receipt_ids == original.expenses[0].receipt_ids


async def test_forward_move_and_explicit_order_keep_other_days_and_anchor_suggested():
    plan = move_itinerary()
    preview = await preview_linked_replan(plan, move("night", 1, 2, "museum"), REQ, SETTINGS)
    assert not preview.conflicts
    ids = [a.id for a in preview.itinerary.days[1].activities]
    assert ids.index("park") < ids.index("night") < ids.index("museum")
    assert preview.itinerary.days[1].activities[0].is_context_anchor
    assert preview.itinerary.days[1].activities[0].progress == "not_started"
    assert preview.itinerary.expenses == plan.expenses


async def test_pretrip_move_without_current_position_and_empty_source_day():
    plan = move_itinerary()
    plan.days[0].replan_context = None
    plan.days[0].activities = [a for a in plan.days[0].activities if a.id == "night"]
    plan.days[1].activities = [plan.days[1].activities[0]]
    result = await preview_linked_replan(plan, move(), REQ, SETTINGS)
    assert not result.conflicts
    assert any(a.id == "park" for a in result.itinerary.days[0].activities)
    assert all(a.is_context_anchor for a in result.itinerary.days[1].activities)


async def test_move_same_named_place_as_past_visit_keeps_both_stable_ids():
    plan = move_itinerary()
    old = plan.days[0].activities[0]
    park = plan.days[1].activities[0]
    park.title, park.place_name = old.title, old.place_name
    result = await preview_linked_replan(plan, move(), REQ, SETTINGS)
    assert not result.conflicts
    assert {"park", "past"} <= {a.id for a in result.itinerary.days[0].activities}


def test_changed_incoming_edges_discard_old_costs_and_times_and_keep_unaffected():
    plan = move_itinerary()
    plan.days[1].activities[0].transport_cost = 40
    apply_activity_moves(plan, move())
    moved = next(a for a in plan.days[0].activities if a.id == "park")
    night = next(a for a in plan.days[0].activities if a.id == "night")
    assert moved.transport_minutes is None and moved.transport_cost is None
    assert moved.start_time is None and night.transport_minutes is None
    assert plan.days[0].activities[-1].transport_minutes == 30
    assert plan.days[1].activities[0].transport_minutes is None


@pytest.mark.parametrize(
    "reason",
    [
        "completed",
        "in_progress",
        "skipped",
        "anchor",
        "booking",
        "fixed",
        "hotel",
        "city",
        "rail",
        "missing",
        "duplicate",
        "nonadjacent",
        "past_position",
    ],
)
async def test_invalid_moves_leave_original_unchanged(reason):
    plan = move_itinerary(booked=True)
    data = move()
    park = plan.days[1].activities[0]
    if reason in {"completed", "in_progress", "skipped"}:
        park.progress = reason
    elif reason == "anchor":
        park.is_context_anchor = True
    elif reason == "booking":
        park.booking = plan.days[1].activities[1].booking
    elif reason == "fixed":
        park.time_is_suggested = False
    elif reason == "hotel":
        park.kind = "hotel"
    elif reason == "city":
        park.city = "上海"
    elif reason == "rail":
        park.transport_mode = "rail"
    elif reason == "missing":
        data.moves[0].activity_id = "absent"
    elif reason == "duplicate":
        data.moves.append(data.moves[0].model_copy())
    elif reason == "nonadjacent":
        data.moves[0].destination_day = 3
    else:
        data.moves[0].before_activity_id = "past"
    before = plan.model_dump_json()
    with pytest.raises(InvalidRequest):
        await preview_linked_replan(plan, data, REQ, SETTINGS)
    assert plan.model_dump_json() == before


async def test_unverified_new_edge_before_fixed_booking_blocks_apply():
    plan = move_itinerary(booked=True)
    result = await preview_linked_replan(plan, move(), REQ, SETTINGS)
    assert any("未核实的交通耗时" in c for c in result.conflicts)
    assert not clarification_prompt(result)["options"]
    assert (
        next(a for a in result.itinerary.days[1].activities if a.id == "museum").booking
        == plan.days[1].activities[1].booking
    )


@pytest.mark.parametrize(
    "message",
    [
        "把第2天公园漫步移到第1天",
        "把第二天第1站移到第一天第3站前",
        "把第2天第1站「公园漫步」移到第1天，预留8小时休息",
    ],
)
async def test_explicit_move_phrases_work_without_model(message):
    reply = await handle_move_message(message, move_itinerary(), REQ, SETTINGS)
    assert reply and reply.preview and not reply.preview.conflicts


@pytest.mark.parametrize(
    "message",
    [
        "如果把第2天公园移到第1天",
        "不要把第2天公园移到第1天",
        "把第2天公园移到第1天，花了20元",
        "把第2天第1站「错误名称」移到第1天",
        "把第2天公园移到第3天第99站前",
        "把第2天公园移到第1天，顺便换酒店",
    ],
)
async def test_hypothetical_mixed_or_stale_request_never_produces_preview(message):
    reply = await handle_move_message(message, move_itinerary(), REQ, SETTINGS)
    assert reply and not reply.preview and not reply.pending


async def test_default_destination_follows_context_even_if_old_unstarted_points_precede_it():
    plan = move_itinerary()
    plan.days[0].activities.insert(
        0,
        ScheduledActivity(
            id="old-unstarted", title="前段待核对景点", start_time="09:00", duration_minutes=30
        ),
    )
    preview = await preview_linked_replan(plan, move(), REQ, SETTINGS)
    ids = [a.id for a in preview.itinerary.days[0].activities]
    assert ids.index("station") < ids.index("park") < ids.index("night")


async def test_chat_ambiguous_refresh_apply_retry_and_payment_edit(environment, monkeypatch):
    client, store, graph, _ = environment
    _, bid, say = await seed(environment, ambiguous=True)
    before = (await graph.aget_state(thread_config(bid))).values["itinerary"]

    async def offline(*args, **kwargs):
        raise AssertionError("明确搬移不调用模型")

    monkeypatch.setattr(FakeLLM, "structured", offline)
    result = await say("把第2天公园漫步移到第1天")
    assert result["clarification"]["kind"] == "move_activity"
    state = (await graph.aget_state(thread_config(bid))).values
    pending = state["pending_move"]
    assert build_serde().loads_typed(build_serde().dumps_typed(pending)) == pending
    assert (await client.get(f"/api/branches/{bid}/messages")).json()["clarification"] == result[
        "clarification"
    ]
    assert (await say("选第99个"))["clarification"]["kind"] == "move_activity"
    preview = await say("选第1个")
    assert preview["clarification"]["kind"] == "linked_replan"
    assert not preview["clarification"]["conflicts"]
    assert len(await store.list_versions(bid)) == 1
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"] == before
    saved = await say("应用两天调整", request_id="move-apply-once")
    assert await say("应用两天调整", request_id="move-apply-once") == saved
    assert len(await store.list_versions(bid)) == 2
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert current.expenses == before.expenses
    assert any(a.id == "park" for a in current.days[0].activities)
    assert any(a.id == "park-other" for a in current.days[1].activities)
    exported = await client.get(f"/api/versions/{saved['version_id']}/export?format=html")
    assert "跨天搬移" in exported.text and "关联行程：第 1 天" in exported.text
    await say("行程日第1天预付公园门票消费改成18元")
    after = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert after.expenses[0].day == 2 and after.expenses[0].amount_cents == 1800


async def test_pending_move_cancels_and_is_cleared_on_fork_manual_edit_and_new_topic(environment):
    client, store, graph, _ = environment
    sid, bid, say = await seed(environment, ambiguous=True)
    await say("把第2天公园漫步移到第1天")
    await say("取消这次操作")
    assert len(await store.list_versions(bid)) == 1
    assert (await graph.aget_state(thread_config(bid))).values["pending_move"] is None
    await say("把第2天公园漫步移到第1天")
    fork = (
        await client.post(
            f"/api/sessions/{sid}/branches", json={"title": "另一个方案", "parent_branch_id": bid}
        )
    ).json()
    new = next(b for b in fork["items"] if b["title"] == "另一个方案")
    assert (await graph.aget_state(thread_config(new["id"]))).values["pending_move"] is None
    version = (await store.list_versions(bid))[-1]
    edited = await client.put(
        f"/api/versions/{version.id}/expenses/fixture-linked-expense",
        json={"title": "预付公园门票", "amount": "18", "day": 2, "category": "ticket"},
    )
    assert edited.status_code == 200, edited.text
    assert (await graph.aget_state(thread_config(bid))).values["pending_move"] is None
    await say("把第2天公园漫步移到第1天")
    await say("第1天地铁花了5元")
    assert (await graph.aget_state(thread_config(bid))).values["pending_move"] is None


async def test_pending_selection_with_changed_source_is_not_reused():
    plan = move_itinerary(ambiguous=True)
    reply = await handle_move_message("把第2天公园漫步移到第1天", plan, REQ, SETTINGS)
    assert reply and reply.pending
    plan.days[1].activities[0].title = "变化后的地点"
    stale = await handle_move_message("选第1个", plan, REQ, SETTINGS, reply.pending)
    assert stale and not stale.preview and "已改变" in stale.reply
