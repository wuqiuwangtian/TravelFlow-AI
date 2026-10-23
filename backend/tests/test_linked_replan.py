import json

import pytest
from conftest import MARKDOWN, FakeLLM
from linked_fixtures import linked_itinerary

from app.agents.checkpoint import build_serde, thread_config
from app.agents.nodes.refiner import OnTripUpdate, RefineIntent, RevisionOutput
from app.agents.state import initial_state
from app.core.config import Settings
from app.core.errors import InvalidRequest
from app.domain.clarification import clarification_prompt
from app.domain.enums import PlanStatus
from app.domain.itinerary import ReplanContext, ScheduledActivity
from app.domain.linked_replan import LinkedReplanInput
from app.domain.models import Requirements
from app.services.chat_service import ChatService
from app.services.itinerary_service import sync_markdown
from app.services.linked_replan_messages import linked_input
from app.services.linked_replan_service import preview_linked_replan

SETTINGS = Settings(_env_file=None, amap_api_key=None)
REQ = Requirements(
    destination="北京", origin="上海", duration_days=3, budget=2000, budget_basis="total"
)


async def seed(environment, *, early_booking=False):
    client, store, graph, _ = environment
    session = await store.create_session("两天联动验收")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    itinerary = linked_itinerary(early_booking=early_booking)
    values = {
        **initial_state(),
        "requirements": REQ,
        "itinerary": itinerary,
        "plan_markdown": sync_markdown(MARKDOWN.replace("[1]", ""), itinerary, REQ),
        "status": PlanStatus.REVIEWING,
    }
    config = await graph.aupdate_state(thread_config(branch.id), values, as_node="planner")
    await ChatService(graph=graph, store=store).record_version(branch.id, values, config)

    async def say(message, *, request_id=None, stream=False):
        response = await client.post(
            "/api/chat/stream" if stream else "/api/chat",
            json={
                "session_id": session.id,
                "branch_id": branch.id,
                "message": message,
                "request_id": request_id,
            },
        )
        assert response.status_code == 200, response.text
        if stream:
            assert "event: done" in response.text, response.text
            return json.loads(response.text.split("event: done\ndata: ")[-1].split("\n\n")[0])
        return response.json()

    return session.id, branch.id, say


async def test_preview_links_two_days_without_mutating_source_or_actual_records():
    original = linked_itinerary(with_receipt=True)
    before = original.model_dump_json()
    preview = await preview_linked_replan(original, LinkedReplanInput(first_day=1), REQ, SETTINGS)
    assert not preview.conflicts
    result = preview.itinerary
    assert result.days[0].activities[-1].start_time == "01:20"
    assert result.days[0].activities[-1].start_day_offset == 1
    assert result.days[1].replan_context.time == "09:50"
    assert all(a.progress == "not_started" for a in result.days[1].activities)
    assert next(a for a in result.days[1].activities if a.id == "park").start_time == "10:20"
    museum = next(a for a in result.days[1].activities if a.id == "museum")
    assert museum.booking == original.days[1].activities[1].booking
    assert museum.start_time == "14:00"
    assert result.days[0].activities[0] == original.days[0].activities[0]
    assert result.days[2] == original.days[2]
    assert result.expenses == original.expenses
    assert result.adjustments == original.adjustments
    assert result.budget_items == original.budget_items
    assert original.model_dump_json() == before
    assert {c.day for c in preview.changes} == {1, 2}
    assert any(
        c.title == "公园漫步" and c.status == "shifted" and c.protected for c in preview.changes
    )
    encoded = build_serde().dumps_typed(preview)
    assert build_serde().loads_typed(encoded) == preview
    assert clarification_prompt(preview)["options"][0]["reply"] == "应用两天调整"


async def test_optional_stops_can_be_omitted_but_bookings_logistics_and_paid_stops_survive():
    original = linked_itinerary()
    original.days[1].activities.insert(
        1,
        ScheduledActivity(
            id="optional",
            title="可选绕路公园",
            place_name="另一个公园",
            city="北京",
            start_time="10:00",
            duration_minutes=600,
            transport_minutes=30,
        ),
    )
    preview = await preview_linked_replan(original, LinkedReplanInput(first_day=1), REQ, SETTINGS)
    assert not preview.conflicts
    ids = {a.id for a in preview.itinerary.days[1].activities}
    assert {"park", "museum", "hotel-two"} <= ids and "optional" not in ids
    assert next(c for c in preview.changes if c.title == "可选绕路公园").status == "omitted"
    assert preview.itinerary.days[1].replan_context.omitted_titles == ["可选绕路公园"]


async def test_early_fixed_booking_blocks_apply_without_changing_its_time():
    original = linked_itinerary(early_booking=True)
    preview = await preview_linked_replan(original, LinkedReplanInput(first_day=1), REQ, SETTINGS)
    assert any("预约冲突" in c and "09:00" in c for c in preview.conflicts)
    assert clarification_prompt(preview)["options"] == []
    assert (
        next(a for a in preview.itinerary.days[1].activities if a.id == "museum").booking.start_time
        == "09:00"
    )
    assert original.days[1].activities[0].start_time == "08:00"


async def test_manual_fixed_station_is_protected_even_without_booking():
    original = linked_itinerary()
    original.days[1].activities.insert(
        0,
        ScheduledActivity(
            id="fixed-rail",
            title="固定发车安排",
            kind="station",
            start_time="08:30",
            duration_minutes=20,
            transport_minutes=30,
            time_is_suggested=False,
        ),
    )
    preview = await preview_linked_replan(original, LinkedReplanInput(first_day=1), REQ, SETTINGS)
    assert any("固定安排冲突" in c for c in preview.conflicts)
    fixed = next(a for a in preview.itinerary.days[1].activities if a.id == "fixed-rail")
    assert fixed.start_time == "08:30" and not fixed.time_is_suggested


@pytest.mark.parametrize("departure,conflict", [("10:30", False), ("08:00", True)])
async def test_explicit_departure_is_respected_and_too_short_rest_is_reported(departure, conflict):
    preview = await preview_linked_replan(
        linked_itinerary(), LinkedReplanInput(first_day=1, next_departure=departure), REQ, SETTINGS
    )
    assert bool(preview.conflicts) == conflict
    assert preview.itinerary.days[1].replan_context.time == departure


async def test_zero_rest_and_unknown_traffic_are_explicit_suggestions():
    original = linked_itinerary()
    original.days[1].activities[0].transport_minutes = None
    preview = await preview_linked_replan(
        original, LinkedReplanInput(first_day=1, rest_minutes=0), REQ, SETTINGS
    )
    assert "0小时" in preview.prompt
    assert preview.itinerary.days[1].replan_context.time == "08:00"
    assert any("交通耗时尚未查到" in w for w in preview.warnings)
    assert any("固定项目" in c and "未核实" in c for c in preview.conflicts)


async def test_previous_end_deadline_is_not_silently_lost():
    original = linked_itinerary()
    original.days[1].replan_context = ReplanContext(
        activity_id="park", time="08:00", end_time="09:00"
    )
    preview = await preview_linked_replan(original, LinkedReplanInput(first_day=1), REQ, SETTINGS)
    assert any("原结束期限09:00" in c for c in preview.conflicts)


@pytest.mark.parametrize("reason", ["missing_day", "history", "unknown_time", "third_day"])
async def test_unavailable_or_out_of_scope_proposals_never_mutate_source(reason):
    original = linked_itinerary()
    if reason == "missing_day":
        original.days = [original.days[0], original.days[2]]
    elif reason == "history":
        original.days[1].activities[0].progress = "completed"
    elif reason == "unknown_time":
        original.days[0].replan_context = None
        original.days[0].activities[-1].duration_minutes = None
    else:
        original.days[0].replan_context = None
        original.days[0].activities[-1].start_day_offset = 2
    before = original.model_dump_json()
    with pytest.raises(InvalidRequest):
        await preview_linked_replan(original, LinkedReplanInput(first_day=1), REQ, SETTINGS)
    assert original.model_dump_json() == before


@pytest.mark.parametrize(
    "message",
    [
        "联动调整第1天和第3天",
        "联动调整第1天和第2天，预留25小时休息",
        "联动调整第1天和第2天，预留-1小时休息",
        "联动调整第1天和第2天，次日10:00出发，次日11:00出发",
        "联动调整第1天和第2天，次日25:00出发",
        "联动调整第1天和第2天，把公园移到明天",
    ],
)
def test_input_rejects_non_adjacent_dates_invalid_times_and_unimplemented_moves(message):
    with pytest.raises(InvalidRequest):
        linked_input(message, linked_itinerary())


async def test_chat_preview_refresh_apply_and_retry_save_one_complete_version(
    environment, monkeypatch
):
    client, store, graph, _ = environment
    _, bid, say = await seed(environment)
    before = (await graph.aget_state(thread_config(bid))).values["itinerary"]

    async def offline(*args, **kwargs):
        raise AssertionError("明确的联动预览与应用不应调用模型")

    monkeypatch.setattr(FakeLLM, "structured", offline)
    result = await say("联动调整第1天和第2天，预留8小时休息", stream=True)
    assert result["clarification"]["kind"] == "linked_replan"
    assert len(await store.list_versions(bid)) == 1
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"] == before
    assert (await client.get(f"/api/branches/{bid}/messages")).json()["clarification"] == result[
        "clarification"
    ]
    applied = await say("应用两天调整", request_id="apply-linked-001", stream=True)
    assert applied["clarification"] is None
    assert await say("应用两天调整", request_id="apply-linked-001") == applied
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert current.days[0].activities[-1].start_time == "01:20"
    assert current.days[1].replan_context.time == "09:50"
    assert current.days[2] == before.days[2]
    assert current.expenses == before.expenses
    assert len(await store.list_versions(bid)) == 2
    detail = await store.get_version_detail(applied["version_id"])
    assert "最近两天联动" in detail.markdown and "09:50" in detail.markdown
    exported = await client.get(f"/api/versions/{applied['version_id']}/export?format=html")
    assert "09:50" in exported.text and "fixture-linked-expense" not in exported.text
    await say("咖啡花了5元")
    recorded = (await graph.aget_state(thread_config(bid))).values["itinerary"].expenses[-1]
    assert recorded.day == 1  # 次日的建议起点不会冒充已到访并劫持当前记账日期。


async def test_conflicts_cannot_apply_and_cancel_leaves_no_partial_version(environment):
    _, store, graph, _ = environment
    _, bid, say = await seed(environment, early_booking=True)
    before = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    preview = await say("联动调整第1天和第2天")
    assert preview["clarification"]["conflicts"]
    assert not preview["clarification"]["options"]
    blocked = await say("应用两天调整")
    assert blocked["clarification"] and "还有冲突" in blocked["reply"]
    await say("取消这次操作")
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"] == before
    assert (await graph.aget_state(thread_config(bid))).values["pending_linked"] is None
    assert len(await store.list_versions(bid)) == 1


@pytest.mark.parametrize("answer", ["确认", "好的，应用两天调整"])
async def test_preview_question_and_short_confirmation_keep_trip_reviewing(environment, answer):
    _, store, graph, _ = environment
    _, bid, say = await seed(environment)
    await say("联动调整第1天和第2天")
    before = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    result = await say("应用两天调整会改记账吗？")
    assert result["clarification"]["kind"] == "linked_replan"
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"] == before
    assert len(await store.list_versions(bid)) == 1
    applied = await say(answer)
    assert applied["clarification"] is None and applied["status"] == "reviewing"
    assert len(await store.list_versions(bid)) == 2


async def test_new_topic_fork_and_manual_edit_invalidate_preview(environment):
    client, store, graph, _ = environment
    sid, bid, say = await seed(environment)
    await say("联动调整第1天和第2天")
    fork = (
        await client.post(
            f"/api/sessions/{sid}/branches", json={"title": "新分支", "parent_branch_id": bid}
        )
    ).json()
    new = next(b for b in fork["items"] if b["title"] == "新分支")
    assert (await graph.aget_state(thread_config(new["id"]))).values["pending_linked"] is None
    version = (await store.list_versions(bid))[-1]
    edited = await client.put(
        f"/api/versions/{version.id}/expenses/fixture-linked-expense",
        json={"title": "预付公园门票", "amount": "18", "day": 2, "category": "ticket"},
    )
    assert edited.status_code == 200, edited.text
    assert (await graph.aget_state(thread_config(bid))).values["pending_linked"] is None
    assert "没有可应用" in (await say("应用两天调整"))["reply"]
    await say("联动调整第1天和第2天")
    await say("第1天地铁花了5元")
    assert (await graph.aget_state(thread_config(bid))).values["pending_linked"] is None
    assert "没有可应用" in (await say("应用两天调整"))["reply"]


@pytest.mark.parametrize(
    "text",
    [
        "第1天23:50我在北京站，次日03:00前回示例酒店，联动第2天",
        "第1天23:50我在北京站，次日03:00前回示例酒店，第1天和第2天一起调整",
    ],
)
async def test_new_position_in_chat_changes_both_days_before_single_apply(
    environment, monkeypatch, text
):
    _, _, graph, _ = environment
    _, bid, say = await seed(environment)
    original = FakeLLM.structured

    async def model(self, messages, schema, **kwargs):
        if schema is RefineIntent:
            return RefineIntent(
                intent="modify",
                on_trip=OnTripUpdate(
                    day=1,
                    time="23:50",
                    location_name="北京站",
                    end_time="03:00",
                    end_day_offset=1,
                    end_location_name="示例酒店",
                ),
            )
        if schema is RevisionOutput:
            raise AssertionError("时间联动由程序校验，不需要模型重写整份行程")
        return await original(self, messages, schema, **kwargs)

    monkeypatch.setattr(FakeLLM, "structured", model)
    result = await say(text)
    assert result["clarification"]["kind"] == "linked_replan"
    await say("应用两天调整")
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert current.days[0].replan_context.time == "23:50"
    assert current.days[1].replan_context.time == "10:10"


@pytest.mark.parametrize(
    "text",
    [
        "如果联动调整第1天和第2天会怎样？",
        "不要联动调整第1天和第2天",
        "第1天地铁花了5元，联动调整第1天和第2天",
    ],
)
async def test_questions_negatives_and_mixed_ledger_requests_do_not_save_partial_changes(
    environment, text
):
    _, store, graph, _ = environment
    _, bid, say = await seed(environment)
    before = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    result = await say(text)
    assert result["clarification"] is None
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"] == before
    assert len(await store.list_versions(bid)) == 1
