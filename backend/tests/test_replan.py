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
    assert result.days[0].activities[-1].start_time == "17:30"


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


async def test_chat_ontrip_update_is_scoped_and_preserves_booked_stop(environment, monkeypatch):
    from conftest import FakeLLM

    from app.agents.nodes.refiner import OnTripUpdate, RefineIntent, RevisionOutput

    client, _, graph, _ = environment
    sid, bid, before = await generate(client)
    original_call = FakeLLM.structured

    async def model(self, messages, schema, **kwargs):
        if schema is RefineIntent:
            return RefineIntent(
                intent="modify",
                on_trip=OnTripUpdate(day=2, location_name="故宫", time="14:00", end_time="18:00"),
            )
        if schema is RevisionOutput:
            return RevisionOutput(
                markdown="模型错误改动了其他章节",
                days=[
                    DayDraft(
                        day=2,
                        activities=[
                            {
                                "title": "超长可选活动",
                                "place_name": "新公园",
                                "city": "北京",
                                "duration_minutes": 300,
                            }
                        ],
                    )
                ],
            )
        return await original_call(self, messages, schema, **kwargs)

    monkeypatch.setattr(FakeLLM, "structured", model)
    booked = await client.post(
        "/api/chat",
        json={"session_id": sid, "branch_id": bid, "message": "第2天第2站已预约16:00，停留60分钟"},
    )
    assert booked.status_code == 200, booked.text
    assert "16:00" in booked.json()["reply"]
    response = await client.post(
        "/api/chat",
        json={
            "session_id": sid,
            "branch_id": bid,
            "message": "第2天14:00，我在故宫，18:00前结束，调整后续行程",
        },
    )
    assert response.status_code == 200, response.text
    after = (await client.get(f"/api/versions/{response.json()['version_id']}")).json()
    assert after["itinerary"]["days"][0] == before["itinerary"]["days"][0]
    assert after["itinerary"]["days"][2] == before["itinerary"]["days"][2]
    day = after["itinerary"]["days"][1]
    assert len(day["activities"]) == 2
    assert not any(a["is_context_anchor"] for a in day["activities"])
    assert day["activities"][1]["booking"]["start_time"] == "16:00"
    assert day["replan_context"]["omitted_titles"] == ["超长可选活动"]
    assert "模型错误改动了其他章节" not in after["markdown"]


async def test_booking_chat_needs_no_model_and_can_clear_record(environment, monkeypatch):
    from conftest import FakeLLM

    client, _, _, _ = environment
    sid, bid, before = await generate(client)

    async def offline(*args, **kwargs):
        raise AssertionError("记录预约不应调用模型")

    monkeypatch.setattr(FakeLLM, "structured", offline)
    for text in ["第2天第2站已预约16:00，停留60分钟", "清除预约记录，第2天第2站"]:
        response = await client.post(
            "/api/chat", json={"session_id": sid, "branch_id": bid, "message": text}
        )
        assert response.status_code == 200, response.text
    after = (await client.get(f"/api/versions/{response.json()['version_id']}")).json()
    assert after["itinerary"]["days"][1]["activities"][1]["booking"] is None
    assert after["itinerary"]["days"][0] == before["itinerary"]["days"][0]
    assert "外部预约没有取消" in response.json()["reply"]


async def test_research_failure_uses_existing_sources_for_revision(environment, monkeypatch):
    from conftest import FakeSearch

    from app.core.errors import SearchUnavailable

    client, _, _, _ = environment
    sid, bid, before = await generate(client)

    async def offline(*args, **kwargs):
        raise SearchUnavailable("测试离线")

    monkeypatch.setattr(FakeSearch, "search", offline)
    result = await client.post(
        "/api/chat", json={"session_id": sid, "branch_id": bid, "message": "换一家酒店"}
    )
    assert result.status_code == 200, result.text
    latest = (await client.get(f"/api/versions/{result.json()['version_id']}")).json()
    assert len(latest["citations"]) == len(before["citations"])


async def test_starting_at_booked_place_does_not_discard_future_booking():
    plan = itinerary()
    record_booking(plan, 1, "booking", Booking(start_time="16:00", duration_minutes=60))
    set_replan_context(
        plan,
        1,
        ReplanInput(activity_id="booking", time="14:00", end_time="18:00", end_activity_id="end"),
    )
    result = await enrich_itinerary(plan, Requirements(), Settings(_env_file=None), day_number=1)
    day = result.days[0]
    context_index = next(
        i for i, a in enumerate(day.activities) if a.id == day.replan_context.activity_id
    )
    booked_index = next(i for i, a in enumerate(day.activities) if a.id == "booking")
    assert context_index < booked_index
    assert day.activities[booked_index].booking.start_time == "16:00"
    assert day.activities[booked_index].start_time == "16:00"


async def test_skipped_booking_stays_in_history_without_requiring_a_visit():
    from app.services.replan_service import preserve_replan_scope

    plan = itinerary()
    record_booking(plan, 1, "booking", Booking(start_time="16:00", duration_minutes=60))
    plan.days[0].activities[3].progress = "skipped"
    set_replan_context(
        plan,
        1,
        ReplanInput(activity_id="anchor", time="17:00", end_time="18:00", end_activity_id="end"),
    )
    preserve_replan_scope(plan.model_copy(deep=True), plan, 1)
    result = await enrich_itinerary(plan, Requirements(), Settings(_env_file=None), day_number=1)
    day = result.days[0]
    booked = next(i for i, a in enumerate(day.activities) if a.id == "booking")
    anchor = next(i for i, a in enumerate(day.activities) if a.id == "anchor")
    assert booked < anchor
    assert day.activities[booked].progress == "skipped"
    assert not any("预约冲突" in w for w in day.warnings)
