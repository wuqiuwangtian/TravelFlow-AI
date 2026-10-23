import asyncio

import httpx
import respx
from test_activity_moves import seed
from test_journey_completion import close
from test_receipts import receipt_image
from test_travel_career import snapshot, trip
from test_travel_photos import payload, upload_photo

from app.core.config import Settings, get_settings
from app.domain.itinerary import RecapPhoto, TravelRecap


async def footprints(client, query=""):
    response = await client.get("/api/travel-career/footprints" + query)
    assert response.status_code == 200, response.text
    return response.json()


async def memories(client, query=""):
    response = await client.get("/api/travel-career/memories" + query)
    assert response.status_code == 200, response.text
    return response.json()


async def test_footprints_only_completed_stays_and_confirmed_coordinates_with_traceable_sources(
    environment,
):
    client, store, _, _ = environment
    sid, bid, _, plan = await trip(store)
    park = plan.days[1].activities[0]
    park.progress = "completed"
    for aid, status, kind, progress, anchor in [
        ("ambiguous", "ambiguous", "attraction", "completed", False),
        ("repeat", "matched", "attraction", "completed", False),
        ("station", "matched", "station", "completed", False),
        ("transport", "matched", "transport", "completed", False),
        ("anchor", "matched", "other", "completed", True),
        ("skipped", "matched", "attraction", "skipped", False),
        ("planned", "matched", "attraction", "not_started", False),
        ("ongoing", "matched", "attraction", "in_progress", False),
    ]:
        activity = park.model_copy(
            deep=True,
            update={
                "id": aid,
                "place_status": status,
                "kind": kind,
                "progress": progress,
                "is_context_anchor": anchor,
            },
        )
        plan.days[1].activities.append(activity)
    vid = await snapshot(store, bid, plan)
    result = await footprints(client)
    assert result["total"] == 4 and result["located_count"] == result["unlocated_count"] == 2
    assert {item["activity_id"] for item in result["items"]} == {
        "past",
        "park",
        "ambiguous",
        "repeat",
    }
    assert len(result["map_points"]) == 1 and result["map_points"][0]["activity_id"] == "park"
    assert all(item["version_id"] == vid and item["session_id"] == sid for item in result["items"])
    ambiguous = next(item for item in result["items"] if item["activity_id"] == "ambiguous")
    assert ambiguous["location"] is None
    assert (await footprints(client, "?city=尚未到访的计划城市"))["total"] == 0
    assert (await footprints(client, "?city=北京"))["total"] == 4


async def test_memories_only_saved_included_photos_keep_user_day_and_do_not_confirm_visits(
    environment,
):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    vid = (await store.list_versions(bid))[-1].id
    first = await upload_photo(client, vid, "red")
    second = await upload_photo(client, vid, "blue")
    private = await upload_photo(client, vid, "green")
    assert (await memories(client))["items"] == []  # 上传本身不保存回顾，更不结束旅行。
    data = await payload(
        client,
        vid,
        [
            {
                "id": first,
                "caption": "用户标注第二天",
                "day": 2,
                "activity_id": "park",
                "included": True,
            },
            {
                "id": second,
                "caption": "用户标注第一天 <script>x</script>",
                "day": 1,
                "included": True,
            },
            {"id": private, "caption": "未选照片的私密说明", "included": False},
        ],
        first,
    )
    saved = (await client.put(f"/api/versions/{vid}/recap", json=data)).json()
    ended = await close(client, saved["version"]["id"])
    result = await memories(client)
    assert result["total"] == 2 and [item["photo_id"] for item in result["items"]] == [
        second,
        first,
    ]
    assert [item["day"] for item in result["items"]] == [1, 2]
    assert result["items"][1]["activity_title"] == "公园漫步"
    assert all(item["version_id"] == ended["version"]["id"] for item in result["items"])
    assert "私密说明" not in str(result) and private not in str(result)
    assert {item["activity_id"] for item in (await footprints(client))["items"]} == {"past"}
    assert (await client.get("/api/travel-career")).json()["items"][0]["photo_count"] == 2
    assert (
        await client.get(f"/api/versions/{ended['version']['id']}/travel-photos/{first}")
    ).status_code == 200


async def test_photo_deselection_keeps_history_and_session_delete_removes_current_memories(
    environment,
):
    client, store, _, _ = environment
    sid, bid, _ = await seed(environment)
    vid = (await store.list_versions(bid))[-1].id
    photo = await upload_photo(client, vid)
    data = await payload(client, vid, [{"id": photo, "included": True}])
    saved = (await client.put(f"/api/versions/{vid}/recap", json=data)).json()
    ended = await close(client, saved["version"]["id"])
    old = ended["version"]["id"]
    assert (await memories(client))["total"] == 1
    data = await payload(client, old, [{"id": photo, "included": False}])
    response = await client.put(f"/api/versions/{old}/recap", json=data)
    assert response.status_code == 200
    assert (await memories(client))["total"] == 0
    assert (await client.get(f"/api/versions/{old}/recap")).json()["photos"][0]["included"]
    assert (await client.get(f"/api/versions/{old}/travel-photos/{photo}")).status_code == 200
    await client.delete(f"/api/sessions/{sid}")
    assert (await footprints(client))["total"] == (await memories(client))["total"] == 0
    assert (await client.get(f"/api/versions/{old}/travel-photos/{photo}")).status_code == 400


async def test_photo_and_footprint_sources_follow_dedup_rule_and_reopen_without_merging_branches(
    environment,
):
    client, store, _, _ = environment
    sid, bid, _, plan = await trip(store)
    plan.recap = TravelRecap(
        title="原回忆",
        body="",
        source_fingerprint="",
        photos=[RecapPhoto(id="a" * 32, included=True)],
    )
    primary_vid = await snapshot(store, bid, plan)
    child = await store.create_branch(sid, "另一份回忆")
    plan.days[1].activities[0].progress = "completed"
    plan.recap.photos = [RecapPhoto(id="b" * 32, included=True)]
    child_vid = await snapshot(store, child.id, plan)
    assert (await memories(client))["items"][0]["version_id"] == primary_vid
    assert (await footprints(client))["located_count"] == 0
    plan.ended_at = None
    await snapshot(store, bid, plan)
    assert (await memories(client))["items"][0]["version_id"] == child_vid
    assert (await footprints(client))["located_count"] == 1
    await snapshot(store, child.id, plan)
    assert (await memories(client))["total"] == (await footprints(client))["total"] == 0


async def test_memories_paginate_all_photos_and_filter_by_source_trip_not_photo_location(
    environment,
):
    client, store, _, _ = environment
    _, bid, _, plan = await trip(store)
    plan.days[0].activities[0].city = "成都"
    plan.recap = TravelRecap(
        title="成都回忆",
        body="",
        source_fingerprint="",
        photos=[
            RecapPhoto(id=f"{index:032x}", included=True, day=1, activity_id="park")
            for index in range(1, 9)
        ],
    )
    # 关联公园尚未完成、位于北京；照片不会将北京写成到访城市。
    await snapshot(store, bid, plan)
    first = await memories(client)
    second = await memories(client, "?page=2")
    assert first["total"] == second["total"] == 8
    assert len(first["items"]) == 6 and len(second["items"]) == 2
    assert not {p["photo_id"] for p in first["items"]} & {p["photo_id"] for p in second["items"]}
    assert (await memories(client, "?city=北京"))["total"] == 0
    assert (await memories(client, "?city=成都"))["total"] == 8
    # 旧活动删除后保留用户原注，不编造关联名称。
    plan.days[1].activities = [a for a in plan.days[1].activities if a.id != "park"]
    await snapshot(store, bid, plan)
    assert (await memories(client))["items"][0]["activity_title"] is None


@respx.mock
async def test_map_limits_and_caches_completed_points_without_planned_paths(
    environment,
):
    client, store, _, _ = environment
    client._transport.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, amap_api_key="career-map-test"
    )
    _, bid, _, plan = await trip(store)
    park = plan.days[1].activities[0]
    park.progress = "completed"
    for i in range(11):
        activity = park.model_copy(deep=True, update={"id": f"confirmed-{i}"})
        activity.place.longitude += (i + 1) / 100
        plan.days[1].activities.append(activity)
    await snapshot(store, bid, plan)
    data = await footprints(client, "?page_size=1")
    assert data["total"] == 13 and data["located_count"] == 12
    assert len(data["items"]) == 1 and len(data["map_points"]) == 10
    upstream = respx.get("https://restapi.amap.com/v3/staticmap").mock(
        return_value=httpx.Response(200, content=receipt_image())
    )
    url = f"/api/travel-career/map?snapshot={data['map_fingerprint']}"
    responses = await asyncio.gather(client.get(url), client.get(url))
    assert all(response.status_code == 200 for response in responses)
    assert len(upstream.calls) == 1 and responses[0].content == responses[1].content
    params = upstream.calls[0].request.url.params
    assert "paths" not in params
    assert params["markers"].count(";") == 9
    assert len(params["labels"].split("|")) == 10
    assert "career-map-test" not in str(data)
    assert responses[0].headers["cache-control"] == "private, no-store"


@respx.mock
async def test_changed_footprint_snapshot_rejected_and_missing_map_service_keeps_records(
    environment,
):
    client, store, _, _ = environment
    _, bid, _, plan = await trip(store)
    plan.days[1].activities[0].progress = "completed"
    await snapshot(store, bid, plan)
    first = await footprints(client)
    no_key = await client.get(f"/api/travel-career/map?snapshot={first['map_fingerprint']}")
    assert no_key.status_code == 400
    assert (await footprints(client))["items"] == first["items"]
    plan.days[1].activities[0].progress = "skipped"
    await snapshot(store, bid, plan)
    stale = await client.get(f"/api/travel-career/map?snapshot={first['map_fingerprint']}")
    assert stale.status_code == 409
    second = await footprints(client)
    assert second["map_points"] == []
    assert (
        await client.get(f"/api/travel-career/map?snapshot={second['map_fingerprint']}")
    ).status_code == 400
    assert (await client.get("/api/travel-career/map?snapshot=not-a-snapshot")).status_code == 400
    assert not respx.calls


@respx.mock
async def test_map_provider_failure_keeps_safe_footprint_message_and_records(environment):
    client, store, _, _ = environment
    client._transport.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, amap_api_key="career-map-test"
    )
    _, bid, _, plan = await trip(store)
    plan.days[1].activities[0].progress = "completed"
    await snapshot(store, bid, plan)
    before = await footprints(client)
    respx.get("https://restapi.amap.com/v3/staticmap").mock(
        return_value=httpx.Response(503, text="upstream unavailable")
    )
    response = await client.get(f"/api/travel-career/map?snapshot={before['map_fingerprint']}")
    assert response.status_code == 502 and "已保留到访记录" in response.text
    assert "行程连线" not in response.text and "career-map-test" not in response.text
    assert await footprints(client) == before
