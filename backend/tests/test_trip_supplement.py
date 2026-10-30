import json

import pytest
from conftest import FakeLLM
from test_itinerary_api import generate

from app.agents.nodes.refiner import RefineIntent, RevisionOutput
from app.domain.itinerary import Activity, Booking, DayDraft, ReplanInput, ScheduledActivity
from app.domain.models import Requirements
from app.services.replan_service import (
    explicit_days,
    preserve_replan_scope,
    record_booking,
    set_replan_context,
)


@pytest.mark.parametrize(
    "text,days",
    [
        ("第1天去程，第三天返程，第一晚住宿", {1, 3}),
        ("第1天上午、第一天下午和第1晚", {1}),
        ("第2晚住朋友家", {2}),
        ("第二天轻松点", {2}),
        ("住宿和往返补完整", set()),
    ],
)
def test_explicit_days_normalizes_repeated_days_and_lodging_nights(text, days):
    assert explicit_days(text) == days


def test_multiple_selected_days_each_keep_their_replan_prefix_and_bookings():
    from test_replan import itinerary

    previous = itinerary()
    third = previous.days[0].model_copy(deep=True)
    third.day = 3
    for activity in third.activities:
        activity.id = "third-" + activity.id
    previous.days.append(third)
    for number, prefix in [(1, ""), (3, "third-")]:
        record_booking(
            previous, number, prefix + "booking", Booking(start_time="16:00", duration_minutes=60)
        )
        set_replan_context(
            previous,
            number,
            ReplanInput(
                activity_id=prefix + "anchor",
                time="14:00",
                end_time="19:00",
                end_activity_id=prefix + "end",
            ),
        )
    original = previous.model_dump()
    current = previous.model_copy(deep=True)
    for day in current.days:
        day.activities = [
            ScheduledActivity(id=f"new-{day.day}", title="新建议", duration_minutes=30)
        ]
    preserve_replan_scope(previous, current, {1, 3})
    assert current.days[1] == previous.days[1]
    for number, prefix in [(1, ""), (3, "third-")]:
        day = next(day for day in current.days if day.day == number)
        assert [a.id for a in day.activities[:2]] == [prefix + "past", prefix + "anchor"]
        assert any(
            a.id == prefix + "booking" and a.booking.start_time == "16:00" for a in day.activities
        )
        assert day.activities[-1].id == prefix + "end"
        assert day.replan_context.time == "14:00"
        assert any(a.id == f"new-{number}" for a in day.activities)
    assert previous.model_dump() == original


@pytest.mark.parametrize("duration", [2, 4])
async def test_explicit_duration_change_can_add_or_remove_a_day(environment, monkeypatch, duration):
    from conftest import drafts

    client, _, _, _ = environment
    sid, _, before = await generate(client)

    async def model(self, messages, schema, **kwargs):
        if schema is RefineIntent:
            return RefineIntent(intent="modify", requirements=Requirements(duration_days=duration))
        days = drafts()
        days.append(DayDraft(day=4, activities=[Activity(title="新增公园", kind="attraction")]))
        return RevisionOutput(markdown="改变天数后的计划", days=days[:duration])

    monkeypatch.setattr(FakeLLM, "structured", model)
    result = await client.post(
        "/api/chat", json={"session_id": sid, "message": f"改为{duration}天，第4天按新天数增减"}
    )
    assert result.status_code == 200, result.text
    after = (await client.get(f"/api/versions/{result.json()['version_id']}")).json()
    assert after["requirements"]["duration_days"] == duration
    assert len(after["itinerary"]["days"]) == duration
    assert (await client.get(f"/api/versions/{before['version']['id']}")).json() == before


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("missing_day", [False, True])
async def test_supplement_selected_days_preserves_other_days_and_records(
    environment, monkeypatch, streaming, missing_day
):
    client, store, _, _ = environment
    failed_attempt = missing_day
    sid, bid, generated = await generate(client)
    day2 = generated["itinerary"]["days"][1]
    response = await client.patch(
        f"/api/versions/{generated['version']['id']}/days/2/activities/{day2['activities'][0]['id']}/booking",
        json={"booking": {"start_time": "14:00", "duration_minutes": 60}},
    )
    assert response.status_code == 200
    current = response.json()
    row = next(
        r for r in current["budget_overview"]["rows"] if r["day"] == 2 and r["source"] == "activity"
    )
    current = (
        await client.put(
            f"/api/versions/{current['version']['id']}/budget-items/{row['id']}",
            json={"amount": "125.00"},
        )
    ).json()
    completed = current["itinerary"]["days"][0]["activities"][0]
    await client.patch(
        f"/api/versions/{current['version']['id']}/days/1/activities/{completed['id']}/progress",
        json={"progress": "completed"},
    )
    paid = (
        await client.post(
            "/api/chat",
            json={
                "session_id": sid,
                "message": "第1天午餐实付86.35元",
                "request_id": "supplement-paid-once",
            },
        )
    ).json()
    baseline = (await client.get(f"/api/versions/{paid['version_id']}")).json()
    before_versions = len(await store.list_versions(bid))
    calls = []

    async def model(self, messages, schema, **kwargs):
        calls.append(schema.__name__)
        if schema is RefineIntent:
            return RefineIntent(intent="modify")
        assert schema is RevisionOutput
        days = [DayDraft.model_validate(day) for day in baseline["itinerary"]["days"]]
        days[0].activities = [
            Activity(title="上海出发站", place_name="上海站", city="上海", kind="station"),
            Activity(
                title="北京到达站",
                place_name="北京站",
                city="北京",
                kind="station",
                transport_mode="rail",
                transport_minutes=120,
            ),
            *days[0].activities,
            Activity(
                title="第一晚过夜住宿", kind="hotel", city="北京", cost=300, cost_basis="total"
            ),
        ]
        days[1].activities[-1].title = "模型擅自改了第二天"
        days[2].activities += [
            Activity(title="北京返程出发站", place_name="北京站", city="北京", kind="station"),
            Activity(
                title="返回上海",
                place_name="上海站",
                city="上海",
                kind="station",
                transport_mode="rail",
                transport_minutes=120,
            ),
        ]
        return RevisionOutput(markdown="补充后", days=days[:1] if missing_day else days)

    monkeypatch.setattr(FakeLLM, "structured", model)
    payload = {
        "session_id": sid,
        "branch_id": bid,
        "request_id": "supplement-days-request",
        "message": (
            "第1晚住宿预估全团300元，第1天补上上海到北京的去程高铁，"
            "第3天补上北京到上海的返程高铁；"
            "票价留未知，现有实付、人工预算和执行记录保持。"
        ),
    }
    endpoint = "/api/chat/stream" if streaming else "/api/chat"
    result = await client.post(endpoint, json=payload)
    if missing_day:
        if streaming:
            assert "event: error" in result.text and "LLM_OUTPUT_INVALID" in result.text
        else:
            assert result.status_code == 422
        assert len(await store.list_versions(bid)) == before_versions
        assert (await store.get_version_detail(baseline["version"]["id"])).model_dump(
            mode="json"
        ) == baseline
        assert (await store.list_versions(bid))[-1].id == baseline["version"]["id"]
        missing_day = False
        result = await client.post(endpoint, json=payload)
    assert result.status_code == 200
    outcome = (
        json.loads(
            next(
                block for block in result.text.split("\n\n") if block.startswith("event: done")
            ).split("data: ", 1)[1]
        )
        if streaming
        else result.json()
    )
    after = (await client.get(f"/api/versions/{outcome['version_id']}")).json()
    assert after["itinerary"]["days"][1] == baseline["itinerary"]["days"][1]
    assert after["itinerary"]["expenses"] == baseline["itinerary"]["expenses"]
    assert after["itinerary"]["budget_items"] == baseline["itinerary"]["budget_items"]
    assert any(
        a["id"] == completed["id"] and a["progress"] == "completed"
        for a in after["itinerary"]["days"][0]["activities"]
    )
    assert all(
        any(
            leg["mode"] == "rail" and leg["cost"] is None
            for leg in after["itinerary"]["days"][day - 1]["legs"]
        )
        for day in [1, 3]
    )
    assert any(
        r["category"] == "lodging" and r["day"] == 1 and r["planned_cents"] == 30000
        for r in after["budget_overview"]["rows"]
    )
    assert not any(
        "去程交通费用安排" in warning or "返程交通费用安排" in warning
        for warning in after["budget_overview"]["coverage_warnings"]
    )
    assert len(await store.list_versions(bid)) == before_versions + 1
    replay = await client.post(endpoint, json=payload)
    assert outcome["version_id"] in replay.text
    assert calls == ["RefineIntent", "RevisionOutput"] * (2 if failed_attempt else 1)
    assert len(await store.list_versions(bid)) == before_versions + 1
    assert (await client.get(f"/api/versions/{baseline['version']['id']}")).json() == baseline
    for fmt in ["md", "html"]:
        export = await client.get(f"/api/versions/{outcome['version_id']}/export?format={fmt}")
        assert "第一晚过夜住宿" in export.text and "返回上海" in export.text
        assert "86.35" in export.text and "125.00" in export.text


@pytest.mark.parametrize(
    "message,accepted", [("第1晚补充住宿安排", True), ("第4天补充返程", False)]
)
async def test_night_only_scope_and_out_of_range_day(environment, monkeypatch, message, accepted):
    from conftest import drafts

    client, store, _, _ = environment
    sid, bid, before = await generate(client)
    calls = []

    async def model(self, messages, schema, **kwargs):
        calls.append(schema.__name__)
        if schema is RefineIntent:
            return RefineIntent(intent="modify")
        days = drafts()
        for day in days:
            day.title = "模型修改后的标题"
        return RevisionOutput(markdown="修改后", days=days)

    monkeypatch.setattr(FakeLLM, "structured", model)
    result = await client.post("/api/chat", json={"session_id": sid, "message": message})
    if not accepted:
        assert result.status_code == 400
        assert calls == ["RefineIntent"]
        assert len(await store.list_versions(bid)) == 1
        return
    assert result.status_code == 200
    after = (await client.get(f"/api/versions/{result.json()['version_id']}")).json()
    assert after["itinerary"]["days"][0]["title"] == "模型修改后的标题"
    assert after["itinerary"]["days"][1:] == before["itinerary"]["days"][1:]


async def test_missing_single_requested_night_cannot_erase_that_day(environment, monkeypatch):
    client, store, _, _ = environment
    sid, bid, before = await generate(client)

    async def model(self, messages, schema, **kwargs):
        if schema is RefineIntent:
            return RefineIntent(intent="modify")
        return RevisionOutput(markdown="漏掉了第一天", days=[DayDraft(day=2)])

    monkeypatch.setattr(FakeLLM, "structured", model)
    result = await client.post(
        "/api/chat", json={"session_id": sid, "message": "第1晚补充住宿安排"}
    )
    assert result.status_code == 422
    assert len(await store.list_versions(bid)) == 1
    assert (await client.get(f"/api/versions/{before['version']['id']}")).json() == before
