import json

import pytest
from conftest import FakeLLM
from place_fixtures import logistics_itinerary

from app.agents.checkpoint import thread_config
from app.agents.itinerary_output import PlanDetails, prepare_itinerary
from app.agents.nodes.refiner import OnTripUpdate, RefineIntent, RevisionOutput
from app.agents.state import initial_state
from app.core.config import Settings
from app.domain.enums import PlanStatus
from app.domain.itinerary import Booking, DayDraft, Place, ReplanInput, RouteLeg
from app.domain.models import Requirements
from app.maps.amap import AmapProvider
from app.services.chat_service import ChatService
from app.services.itinerary_service import enrich_itinerary, sync_markdown
from app.services.replan_service import explicit_logistics_changes, set_replan_context

SETTINGS = Settings(_env_file=None, amap_api_key=None)
REQ = Requirements(origin="上海", destination="北京", duration_days=2, travelers=2)
REQUIRED = {"departure-station", "return-train", "overnight-hotel"}


async def test_tight_deadline_never_auto_removes_transport_or_lodging():
    plan = await enrich_itinerary(logistics_itinerary(), REQ, SETTINGS)
    before = plan.model_dump()
    set_replan_context(
        plan, 2, ReplanInput(activity_id="departure", time="14:00", end_time="14:10")
    )
    result = await enrich_itinerary(plan, REQ, SETTINGS, day_number=2)
    day = result.days[1]
    assert {activity.id for activity in day.activities} >= REQUIRED
    assert "optional-park" not in {activity.id for activity in day.activities}
    assert any("时间冲突" in warning for warning in day.warnings)
    assert result.expenses == plan.expenses and result.budget_items == plan.budget_items
    assert result.days[0].model_dump() == before["days"][0]
    station_leg = next(leg for leg in day.legs if leg.to_id == "departure-station")
    assert station_leg.duration_seconds is station_leg.cost is None
    assert day.replan_context.omitted_titles == ["可选公园"]


async def test_model_omission_cannot_erase_unbooked_logistics_during_ontrip_revision():
    plan = await enrich_itinerary(logistics_itinerary(), REQ, SETTINGS)
    set_replan_context(
        plan, 2, ReplanInput(activity_id="departure", time="14:00", end_time="14:10")
    )
    result, _ = await prepare_itinerary(
        PlanDetails(
            days=[DayDraft(day=2, activities=[{"title": "只剩可选游览", "place_name": "公园"}])]
        ),
        "原计划",
        REQ,
        SETTINGS,
        0,
        previous=plan,
        scope_day=2,
    )
    assert {activity.id for activity in result.days[1].activities} >= REQUIRED


async def test_model_cannot_turn_required_rail_into_a_free_walk():
    plan = await enrich_itinerary(logistics_itinerary(), REQ, SETTINGS)
    set_replan_context(
        plan, 2, ReplanInput(activity_id="departure", time="14:00", end_time="14:10")
    )
    forged = plan.days[1].activities[-2].model_dump()
    forged.update(kind="other", transport_mode="walking", transport_minutes=1, transport_cost=0)
    result, _ = await prepare_itinerary(
        PlanDetails(days=[DayDraft(day=2, activities=[forged])]),
        "原计划",
        REQ,
        SETTINGS,
        0,
        previous=plan,
        scope_day=2,
    )
    target = next(a for a in result.days[1].activities if a.id == "return-train")
    assert target.kind == "station" and target.transport_mode == "rail"
    assert target.transport_minutes == 120 and target.transport_cost is None


@pytest.mark.parametrize(
    "kind,mode",
    [
        ("hotel", "auto"),
        ("station", "auto"),
        ("transport", "auto"),
        ("other", "flight"),
        ("other", "rail"),
        ("other", "transfer"),
    ],
)
async def test_logistics_with_unknown_duration_and_price_survive_even_legacy_context(kind, mode):
    plan = logistics_itinerary()
    protected = plan.days[1].activities[-1]
    protected.kind, protected.transport_mode = kind, mode
    protected.transport_minutes = protected.transport_cost = protected.cost = None
    set_replan_context(
        plan, 2, ReplanInput(activity_id="departure", time="14:00", end_time="14:10")
    )
    plan.days[1].replan_context.preserve_logistics = False
    result = await enrich_itinerary(plan, REQ, SETTINGS, day_number=2)
    target = next(activity for activity in result.days[1].activities if activity.id == protected.id)
    assert target.cost is target.transport_cost is None
    assert any("尚未查到" in warning for warning in result.days[1].warnings)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("第2天取消第5站", {"return-train"}),
        ("第2天不坐高铁返上海了", {"return-train"}),
        ("第2晚换一家酒店", {"overnight-hotel"}),
        ("第2天把过夜住宿换成另一家酒店", {"overnight-hotel"}),
        ("如果第2天取消高铁返上海", set()),
        ("第2天别取消高铁返上海", set()),
        ("第2天不要改酒店", set()),
        ("第2天取消住宿预算", set()),
        ("第1天取消高铁返上海", set()),
        ("第2天取消第2站", set()),
        ("第2天取消第5站可以吗？", set()),
    ],
)
def test_only_explicit_unbooked_target_is_open_to_change(message, expected):
    assert explicit_logistics_changes(message, logistics_itinerary()) == expected


async def test_explicit_cancel_can_remove_one_logistics_stop_while_others_stay():
    plan = await enrich_itinerary(logistics_itinerary(), REQ, SETTINGS)
    set_replan_context(
        plan, 2, ReplanInput(activity_id="departure", time="14:00", end_time="14:10")
    )
    changes = explicit_logistics_changes("第2天取消第5站", plan)
    result, _ = await prepare_itinerary(
        PlanDetails(days=[DayDraft(day=2, activities=[plan.days[1].activities[1].model_dump()])]),
        "原计划",
        REQ,
        SETTINGS,
        0,
        previous=plan,
        scope_day=2,
        changeable_logistics_ids=changes,
    )
    ids = {activity.id for activity in result.days[1].activities}
    assert ids >= REQUIRED - {"return-train"} and "return-train" not in ids
    assert result.expenses == plan.expenses and result.budget_items == plan.budget_items


async def test_booked_hotel_cannot_be_removed_even_if_explicitly_listed_as_changeable():
    plan = await enrich_itinerary(logistics_itinerary(), REQ, SETTINGS)
    hotel = plan.days[1].activities[-1]
    hotel.booking = Booking(start_time="18:00", duration_minutes=60)
    set_replan_context(
        plan, 2, ReplanInput(activity_id="departure", time="14:00", end_time="14:10")
    )
    result, _ = await prepare_itinerary(
        PlanDetails(days=[DayDraft(day=2, activities=[plan.days[1].activities[1].model_dump()])]),
        "原计划",
        REQ,
        SETTINGS,
        0,
        previous=plan,
        scope_day=2,
        changeable_logistics_ids={hotel.id},
    )
    assert (
        next(activity for activity in result.days[1].activities if activity.id == hotel.id).booking
        == hotel.booking
    )


async def test_changed_route_after_optional_removal_uses_fresh_map_result(monkeypatch):
    async def route(self, origin, target, mode):
        return RouteLeg(
            from_id="",
            to_id="",
            mode=mode,
            status="ok",
            duration_seconds=600,
            cost=4,
            cost_basis="total",
            cost_source="reference",
        )

    monkeypatch.setattr(AmapProvider, "route", route)
    plan = logistics_itinerary()
    for activity in plan.days[1].activities:
        activity.place = Place(
            id=activity.id, name=activity.place_name, longitude=116.4, latitude=39.9
        )
        activity.place_status = "matched"
    set_replan_context(
        plan, 2, ReplanInput(activity_id="departure", time="14:00", end_time="14:10")
    )
    result = await enrich_itinerary(
        plan, REQ, Settings(_env_file=None, amap_api_key="test-only"), day_number=2
    )
    day = result.days[1]
    assert "optional-park" not in {activity.id for activity in day.activities}
    station_leg = next(leg for leg in day.legs if leg.to_id == "departure-station")
    assert (
        station_leg.from_id == "dinner"
        and station_leg.duration_seconds == 600
        and station_leg.cost == 4
    )
    rail_leg = next(leg for leg in day.legs if leg.to_id == "return-train")
    assert rail_leg.duration_seconds == 7200 and rail_leg.cost is None


@pytest.mark.parametrize("entry", ["api", "chat", "stream"])
async def test_replan_entry_points_keep_logistics_exports_and_explicit_cancellation(
    environment, monkeypatch, entry
):
    client, store, graph, _ = environment
    session = await store.create_session("必要交通保护")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    plan = await enrich_itinerary(logistics_itinerary(), REQ, SETTINGS)
    values = {
        **initial_state(),
        "requirements": REQ,
        "itinerary": plan,
        "plan_markdown": sync_markdown("原计划", plan, REQ),
        "status": PlanStatus.REVIEWING,
    }
    config = await graph.aupdate_state(thread_config(branch.id), values, as_node="planner")
    await ChatService(graph=graph, store=store).record_version(
        branch.id, values, config, trigger="fixture"
    )
    original = (await store.list_versions(branch.id))[-1]
    before = (await client.get(f"/api/versions/{original.id}")).json()

    async def structured(self, messages, schema, **kwargs):
        if schema is RefineIntent:
            return RefineIntent(
                intent="modify",
                on_trip=OnTripUpdate(
                    day=2, location_name="示例酒店", time="14:00", end_time="14:10"
                )
                if "14:10前结束" in messages[0].content
                else None,
            )
        assert schema is RevisionOutput
        return RevisionOutput(
            markdown="模型省略了交通和住宿",
            days=[DayDraft(day=2, activities=[plan.days[1].activities[1].model_dump()])],
        )

    monkeypatch.setattr(FakeLLM, "structured", structured)

    async def say(message, rid):
        response = await client.post(
            "/api/chat/stream" if entry == "stream" else "/api/chat",
            json={
                "session_id": session.id,
                "branch_id": branch.id,
                "message": message,
                "request_id": rid,
            },
        )
        assert response.status_code == 200
        if entry == "stream":
            assert "event: error" not in response.text
            return json.loads(response.text.split("event: done\ndata: ")[1].split("\n\n")[0])
        return response.json()

    if entry == "api":
        response = await client.post(
            f"/api/versions/{original.id}/days/2/replan",
            json={"activity_id": "departure", "time": "14:00", "end_time": "14:10"},
        )
        assert response.status_code == 200
        vid = response.json()["version"]["id"]
    else:
        reply = await say(
            "第2天14:00我在示例酒店，14:10前结束，只精简可选游览，保留预约和账目",
            "required-replan-once",
        )
        vid = reply["version_id"]
        replay = await say(
            "第2天14:00我在示例酒店，14:10前结束，只精简可选游览，保留预约和账目",
            "required-replan-once",
        )
        assert replay["version_id"] == vid
    detail = (await client.get(f"/api/versions/{vid}")).json()
    assert len(await store.list_versions(branch.id)) == 2
    assert {a["id"] for a in detail["itinerary"]["days"][1]["activities"]} >= REQUIRED
    assert detail["itinerary"]["days"][0] == before["itinerary"]["days"][0]
    assert detail["itinerary"]["expenses"] == before["itinerary"]["expenses"]
    assert detail["itinerary"]["budget_items"] == before["itinerary"]["budget_items"]
    for fmt in ("md", "html"):
        exported = await client.get(f"/api/versions/{vid}/export?format={fmt}")
        assert exported.status_code == 200
        assert all(
            name in exported.text for name in ["北京南站", "高铁返上海", "过夜住宿", "时间冲突"]
        )
    assert (await client.get(f"/api/versions/{original.id}")).json() == before
    cancelled = await say("第2天取消高铁返上海", "explicit-logistics-cancel")
    after = (await client.get(f"/api/versions/{cancelled['version_id']}")).json()
    ids = {a["id"] for a in after["itinerary"]["days"][1]["activities"]}
    assert ids >= REQUIRED - {"return-train"} and "return-train" not in ids
    assert len(await store.list_versions(branch.id)) == 3
