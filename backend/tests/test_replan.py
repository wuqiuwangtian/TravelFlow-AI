from test_itinerary_api import generate

from app.agents.checkpoint import build_serde
from app.agents.itinerary_output import PlanDetails, prepare_itinerary
from app.core.config import Settings
from app.domain.itinerary import (
    Booking,
    DayDraft,
    DayPlan,
    Itinerary,
    ReplanInput,
    ScheduledActivity,
)
from app.domain.models import Requirements
from app.services.itinerary_service import enrich_itinerary
from app.services.replan_service import record_booking, set_replan_context


def itinerary():
    return Itinerary(
        days=[
            DayPlan(
                day=1,
                activities=[
                    ScheduledActivity(
                        id="past",
                        title="已经逛完",
                        place_name="故宫",
                        start_time="09:00",
                        duration_minutes=120,
                        progress="completed",
                    ),
                    ScheduledActivity(
                        id="anchor",
                        title="当前地点",
                        place_name="景山",
                        start_time="12:00",
                        duration_minutes=60,
                    ),
                    ScheduledActivity(
                        id="optional",
                        title="可选公园",
                        place_name="公园",
                        duration_minutes=120,
                        transport_minutes=30,
                    ),
                    ScheduledActivity(
                        id="booking",
                        title="预约博物馆",
                        place_name="博物馆",
                        duration_minutes=60,
                        transport_minutes=30,
                    ),
                    ScheduledActivity(
                        id="end",
                        title="回酒店",
                        place_name="酒店",
                        kind="hotel",
                        duration_minutes=30,
                        transport_minutes=30,
                    ),
                ],
            ),
            DayPlan(
                day=2, title="其他天", activities=[ScheduledActivity(id="other", title="明天景点")]
            ),
        ]
    )


async def test_replan_removes_optional_before_reservation_and_keeps_end():
    plan = itinerary()
    record_booking(plan, 1, "booking", Booking(start_time="16:00", duration_minutes=60))
    set_replan_context(
        plan,
        1,
        ReplanInput(activity_id="anchor", time="14:00", end_time="18:00", end_activity_id="end"),
    )
    old_past = plan.days[0].activities[0].model_dump()
    result = await enrich_itinerary(plan, Requirements(), Settings(_env_file=None), day_number=1)
    assert [a.id for a in result.days[0].activities] == ["past", "anchor", "booking", "end"]
    assert result.days[0].activities[0].model_dump() == old_past
    assert result.days[1] == plan.days[1]
    assert result.days[0].activities[2].start_time == "16:00"
    assert result.days[0].activities[-1].start_time == "17:30"
    assert result.days[0].replan_context.omitted_titles == ["可选公园"]
    # 原方案未修改，可随历史版本恢复。
    assert len(plan.days[0].activities) == 5


async def test_impossible_reservation_is_preserved_and_conflict_is_explicit():
    plan = itinerary()
    record_booking(plan, 1, "booking", Booking(start_time="15:00", duration_minutes=60))
    set_replan_context(
        plan,
        1,
        ReplanInput(activity_id="anchor", time="15:30", end_time="16:00", end_activity_id="end"),
    )
    result = await enrich_itinerary(plan, Requirements(), Settings(_env_file=None), day_number=1)
    assert result.days[0].activities[-2].booking.start_time == "15:00"
    assert result.days[0].activities[-1].id == "end"
    assert any("预约冲突" in w for w in result.days[0].warnings)
    assert any("时间冲突" in w for w in result.days[0].warnings)


async def test_new_position_has_zero_dwell_and_unknown_travel_is_disclosed():
    plan = itinerary()
    set_replan_context(
        plan,
        1,
        ReplanInput(location_name="街边咖啡店", city="北京", time="14:00", end_time="19:00"),
    )
    result = await enrich_itinerary(plan, Requirements(), Settings(_env_file=None), day_number=1)
    anchor = next(a for a in result.days[0].activities if a.is_context_anchor)
    assert anchor.duration_minutes == 0
    assert anchor.start_time == "14:00"
    assert result.days[0].activities[0].id == "past"
    assert any("交通耗时尚未查到" in w for w in result.days[0].warnings)


async def test_model_cannot_delete_appointment_or_rewrite_other_days():
    plan = itinerary()
    record_booking(plan, 1, "booking", Booking(start_time="16:00", duration_minutes=60))
    set_replan_context(
        plan,
        1,
        ReplanInput(activity_id="anchor", time="14:00", end_time="19:00", end_activity_id="end"),
    )
    fake = PlanDetails(
        days=[
            DayDraft(
                day=1,
                activities=[
                    {"title": "新的可选活动", "place_name": "新公园", "duration_minutes": 180}
                ],
            )
        ]
    )
    result, markdown = await prepare_itinerary(
        fake,
        "新版计划",
        Requirements(duration_days=2),
        Settings(_env_file=None),
        0,
        previous=plan,
        scope_day=1,
    )
    assert result.days[1] == plan.days[1]
    assert result.days[0].activities[0].id == "past"
    booking = next(a for a in result.days[0].activities if a.id == "booking")
    assert booking.booking.start_time == booking.start_time == "16:00"
    assert result.days[0].activities[-1].id == "end"
    assert "已记录预约：16:00" in markdown
    serde = build_serde()
    restored = serde.loads_typed(serde.dumps_typed(result))
    assert restored.days[0].replan_context.time == "14:00"
    assert (
        next(a for a in restored.days[0].activities if a.id == "booking").booking.duration_minutes
        == 60
    )


async def test_replan_endpoints_version_history_and_manual_edit_guard(environment):
    client, _, _, _ = environment
    _, _, before = await generate(client)
    old_day = before["itinerary"]["days"][0]
    booked = old_day["activities"][1]
    response = await client.patch(
        f"/api/versions/{before['version']['id']}/days/1/activities/{booked['id']}/booking",
        json={"booking": {"start_time": "16:00", "duration_minutes": 60}},
    )
    assert response.status_code == 200, response.text
    detail = response.json()
    edited = await client.put(
        f"/api/versions/{detail['version']['id']}/days/1/activities/{booked['id']}",
        json={"activity": {**booked, "place_name": "别的公园"}},
    )
    assert edited.status_code == 409
    result = await client.post(
        f"/api/versions/{detail['version']['id']}/days/1/replan",
        json={"activity_id": old_day["activities"][0]["id"], "time": "14:00", "end_time": "18:00"},
    )
    assert result.status_code == 200, result.text
    latest = result.json()
    assert latest["itinerary"]["days"][1:] == before["itinerary"]["days"][1:]
    assert latest["itinerary"]["days"][0]["activities"][1]["booking"]["start_time"] == "16:00"
    assert (await client.get(f"/api/versions/{before['version']['id']}")).json() == before
    exported = await client.get(f"/api/versions/{latest['version']['id']}/export?format=md")
    assert "14:00" in exported.text and "已记录预约：16:00" in exported.text
    stale = await client.post(
        f"/api/versions/{before['version']['id']}/days/1/replan",
        json={"activity_id": old_day["activities"][0]["id"], "time": "14:00"},
    )
    assert stale.status_code == 409
    invalid = await client.post(
        f"/api/versions/{latest['version']['id']}/days/1/replan",
        json={"activity_id": old_day["activities"][0]["id"], "time": "18:00", "end_time": "14:00"},
    )
    assert invalid.status_code == 400
