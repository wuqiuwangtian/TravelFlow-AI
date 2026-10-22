import pytest
from pydantic import ValidationError
from test_itinerary_api import generate

from app.agents.checkpoint import build_serde, thread_config
from app.core.config import Settings
from app.core.errors import InvalidRequest
from app.domain.itinerary import Booking, DayPlan, Itinerary, ReplanContext, ReplanInput
from app.domain.models import Requirements
from app.services.booking_messages import handle_booking_message
from app.services.itinerary_service import enrich_itinerary, schedule_day
from app.services.replan_service import record_booking, set_replan_context


def night_plan():
    return Itinerary.model_validate(
        {
            "days": [
                {
                    "day": 1,
                    "activities": [
                        {
                            "id": "anchor",
                            "title": "出发点",
                            "place_name": "景山",
                            "start_time": "22:00",
                            "duration_minutes": 30,
                        },
                        {
                            "id": "walk",
                            "title": "夜游",
                            "place_name": "夜景步道",
                            "duration_minutes": 60,
                            "transport_minutes": 30,
                        },
                        {
                            "id": "show",
                            "title": "夜场演出",
                            "place_name": "剧场",
                            "duration_minutes": 30,
                            "transport_minutes": 15,
                        },
                        {
                            "id": "hotel",
                            "title": "回酒店",
                            "place_name": "酒店",
                            "kind": "hotel",
                            "duration_minutes": 30,
                            "transport_minutes": 15,
                        },
                    ],
                },
                {
                    "day": 2,
                    "activities": [
                        {
                            "id": "morning",
                            "title": "次日安排",
                            "start_time": "09:00",
                            "duration_minutes": 60,
                        }
                    ],
                },
            ]
        }
    )


async def test_overnight_window_preserves_next_day_booking_and_other_days():
    plan = night_plan()
    record_booking(
        plan, 1, "show", Booking(start_time="00:30", start_day_offset=1, duration_minutes=30)
    )
    old_other = plan.days[1].model_dump()
    set_replan_context(
        plan,
        1,
        ReplanInput(
            activity_id="anchor",
            time="23:00",
            end_time="01:30",
            end_day_offset=1,
            end_activity_id="hotel",
        ),
    )
    result = await enrich_itinerary(plan, Requirements(), Settings(_env_file=None), day_number=1)
    stops = result.days[0].activities
    show = next(a for a in stops if a.id == "show")
    hotel = stops[-1]
    assert show.start_time == "00:30" and show.start_day_offset == 1
    assert hotel.start_time == "01:15" and hotel.start_day_offset == 1
    assert result.days[1].model_dump() == old_other
    assert not any("时间冲突" in w or "预约冲突" in w for w in result.days[0].warnings)
    assert not any(a.start_time == "23:59" for a in stops)


async def test_next_day_deadline_trims_only_optional_and_checks_fixed_conflicts():
    plan = night_plan()
    record_booking(
        plan, 1, "show", Booking(start_time="00:30", start_day_offset=1, duration_minutes=30)
    )
    set_replan_context(
        plan,
        1,
        ReplanInput(
            activity_id="anchor",
            time="23:50",
            end_time="01:00",
            end_day_offset=1,
            end_activity_id="hotel",
        ),
    )
    result = await enrich_itinerary(plan, Requirements(), Settings(_env_file=None), day_number=1)
    assert "夜游" in result.days[0].replan_context.omitted_titles
    assert (
        next(a for a in result.days[0].activities if a.id == "show").booking.start_day_offset == 1
    )
    assert any("次日 01:00" in w and "时间冲突" in w for w in result.days[0].warnings)
    assert result.days[1] == plan.days[1]


@pytest.mark.parametrize(
    "updates", [{}, {"end_day_offset": 2}, {"start_day_offset": 1, "end_day_offset": 0}]
)
def test_ambiguous_or_overlong_windows_are_rejected(updates):
    plan = night_plan()
    before = plan.model_dump()
    data = ReplanInput(activity_id="anchor", time="23:00", end_time="01:00", **updates)
    with pytest.raises(InvalidRequest):
        set_replan_context(plan, 1, data)
    assert plan.model_dump() == before
    with pytest.raises(ValidationError):
        ReplanContext(activity_id="anchor", time="23:00", end_time="01:00", **updates)


async def test_overlap_with_next_travel_day_is_reported_without_moving_it():
    plan = night_plan()
    plan.days[1].activities[0].start_time = "00:45"
    set_replan_context(
        plan,
        1,
        ReplanInput(
            activity_id="anchor",
            time="23:00",
            end_time="03:00",
            end_day_offset=1,
            end_activity_id="hotel",
        ),
    )
    result = await enrich_itinerary(plan, Requirements(), Settings(_env_file=None), day_number=1)
    assert any("跨日衔接冲突" in w for w in result.days[0].warnings)
    assert result.days[1] == plan.days[1]


def test_schedule_keeps_midnight_times_and_booking_parser_requires_explicit_date():
    day = DayPlan.model_validate(
        {
            "day": 1,
            "activities": [
                {"id": "a", "title": "夜游", "start_time": "23:30", "duration_minutes": 90},
                {"id": "b", "title": "回酒店", "duration_minutes": 30},
            ],
        }
    )
    schedule_day(day)
    assert day.activities[1].start_time == "01:00" and day.activities[1].start_day_offset == 1
    plan = night_plan()
    reply = handle_booking_message("第1天第3站已预约次日00:30，停留30分钟", plan)
    assert reply.itinerary.days[0].activities[2].booking.start_day_offset == 1
    assert "次日 00:30" in reply.reply
    assert handle_booking_message("第1天第3站不是已预约次日00:30", plan) is None


async def test_overnight_versions_exports_and_fork_keep_dates(environment):
    client, _, graph, _ = environment
    sid, bid, before = await generate(client)
    vid = before["version"]["id"]
    ids = [a["id"] for a in before["itinerary"]["days"][0]["activities"]]
    booked = (
        await client.patch(
            f"/api/versions/{vid}/days/1/activities/{ids[1]}/booking",
            json={
                "booking": {"start_time": "00:30", "start_day_offset": 1, "duration_minutes": 30}
            },
        )
    ).json()
    response = await client.post(
        f"/api/versions/{booked['version']['id']}/days/1/replan",
        json={"activity_id": ids[0], "time": "23:00", "end_time": "02:00", "end_day_offset": 1},
    )
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["itinerary"]["days"][0]["replan_context"]["end_day_offset"] == 1
    assert after["itinerary"]["days"][0]["activities"][1]["start_day_offset"] == 1
    assert (await client.get(f"/api/versions/{vid}")).json() == before
    for fmt in ("md", "html"):
        exported = await client.get(f"/api/versions/{after['version']['id']}/export?format={fmt}")
        assert "次日 00:30" in exported.text
    forked = await client.post(
        f"/api/sessions/{sid}/branches",
        json={
            "title": "夜间备选",
            "parent_branch_id": bid,
            "fork_version_id": after["version"]["id"],
        },
    )
    branch = next(b for b in forked.json()["items"] if b["title"] == "夜间备选")
    snapshot = await graph.aget_state(thread_config(branch["id"]))
    restored = build_serde().loads_typed(build_serde().dumps_typed(snapshot.values))
    assert restored["itinerary"].days[0].activities[1].booking.start_day_offset == 1


async def test_chat_midnight_extraction_preserves_booking_and_rejects_invented_date(
    environment, monkeypatch
):
    from conftest import FakeLLM, drafts

    from app.agents.nodes.refiner import OnTripUpdate, RefineIntent, RevisionOutput

    client, _, graph, _ = environment
    sid, bid, before = await generate(client)
    original = FakeLLM.structured

    async def structured(self, messages, schema, **kwargs):
        if schema is RefineIntent:
            return RefineIntent(
                intent="modify",
                on_trip=OnTripUpdate(
                    day=1,
                    location_name="故宫博物院",
                    time="23:00",
                    end_time="02:00",
                    end_day_offset=1,
                ),
            )
        if schema is RevisionOutput:
            return RevisionOutput(markdown="夜间安排", days=drafts())
        return await original(self, messages, schema, **kwargs)

    monkeypatch.setattr(FakeLLM, "structured", structured)
    bad = await client.post(
        "/api/chat",
        json={
            "session_id": sid,
            "branch_id": bid,
            "message": "第1天23:00我在故宫博物院，02:00前结束，帮我调整",
        },
    )
    assert bad.status_code == 400 and "跨午夜" in bad.json()["error"]["message"]
    assert (await graph.aget_state(thread_config(bid))).values.get("revision", 0) == 0
    good = await client.post(
        "/api/chat",
        json={
            "session_id": sid,
            "branch_id": bid,
            "message": "第1天23:00我在故宫博物院，次日02:00前结束，帮我调整",
        },
    )
    assert good.status_code == 200, good.text
    detail = (await client.get(f"/api/versions/{good.json()['version_id']}")).json()
    assert detail["itinerary"]["days"][0]["replan_context"]["end_day_offset"] == 1
    assert detail["itinerary"]["days"][1:] == before["itinerary"]["days"][1:]


def test_late_fixed_booking_keeps_following_arrival_after_travel_even_without_context():
    from app.domain.itinerary import RouteLeg

    day = DayPlan.model_validate(
        {
            "day": 1,
            "activities": [
                {"id": "a", "title": "夜游", "start_time": "23:30", "duration_minutes": 120},
                {
                    "id": "b",
                    "title": "预约",
                    "booking": {
                        "start_time": "00:30",
                        "start_day_offset": 1,
                        "duration_minutes": 30,
                    },
                },
                {"id": "c", "title": "酒店", "duration_minutes": 30},
            ],
        }
    )
    day.legs = [
        RouteLeg(from_id="a", to_id="b", mode="walking", duration_seconds=600),
        RouteLeg(from_id="b", to_id="c", mode="walking", duration_seconds=600),
    ]
    schedule_day(day)
    assert day.activities[1].start_time == "00:30" and day.activities[1].start_day_offset == 1
    assert day.activities[2].start_time == "02:20" and day.activities[2].start_day_offset == 1
    assert any("冲突" in w for w in day.warnings)


async def test_recording_an_existing_tomorrow_booking_does_not_need_model(environment, monkeypatch):
    from conftest import FakeLLM

    client, _, _, _ = environment
    sid, bid, _ = await generate(client)

    async def offline(*args, **kwargs):
        raise AssertionError("已有预约记录不应调用模型")

    monkeypatch.setattr(FakeLLM, "structured", offline)
    response = await client.post(
        "/api/chat",
        json={
            "session_id": sid,
            "branch_id": bid,
            "message": "第1天第2站已预约明天00:30，停留30分钟",
        },
    )
    assert response.status_code == 200, response.text
    detail = (await client.get(f"/api/versions/{response.json()['version_id']}")).json()
    assert detail["itinerary"]["days"][0]["activities"][1]["booking"]["start_day_offset"] == 1
