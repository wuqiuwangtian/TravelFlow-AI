import pytest
from move_fixtures import move_itinerary
from test_activity_moves import seed

from app.agents.checkpoint import build_serde, thread_config
from app.services.travel_service import preserve_travel_records


async def close(client, vid, ended=True):
    response = await client.put(f"/api/versions/{vid}/journey-status", json={"ended": ended})
    assert response.status_code == 200, response.text
    return response.json()


async def test_end_creates_recap_keeps_unknowns_and_actual_records_and_retries_once(environment):
    client, store, graph, _ = environment
    sid, bid, _ = await seed(environment)
    vid = (await store.list_versions(bid))[-1].id
    before = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    ended = await close(client, vid)
    now = ended["version"]["id"]
    assert ended["itinerary"]["ended_at"] and ended["itinerary"]["recap"]
    assert ended["itinerary"]["days"] == before.model_dump(mode="json")["days"]
    assert ended["itinerary"]["expenses"] == before.model_dump(mode="json")["expenses"]
    assert ended["itinerary"]["budget_items"] == before.model_dump(mode="json")["budget_items"]
    assert (await close(client, vid))["version"]["id"] == now
    assert len(await store.list_versions(bid)) == 2
    original = (await client.get(f"/api/versions/{vid}")).json()
    assert not original["itinerary"]["ended_at"] and not original["itinerary"]["recap"]
    archive = (await client.get("/api/recap-archive")).json()
    assert archive["total"] == 1 and archive["items"][0]["session_id"] == sid
    assert archive["items"][0]["completed_count"] == 1
    assert archive["items"][0]["unconfirmed_count"] > 0
    assert archive["items"][0]["unsettled_count"] > 0
    exported = await client.get(f"/api/versions/{now}/recap/export?format=html")
    assert "旅行状态：已结束" in exported.text
    current = (await graph.aget_state(thread_config(bid))).values["itinerary"]
    assert build_serde().loads_typed(build_serde().dumps_typed(current)) == current
    regenerated = move_itinerary()
    preserve_travel_records(current, regenerated)
    assert regenerated.ended_at == current.ended_at and regenerated.recap == current.recap


async def test_finish_existing_recap_and_photos_then_edit_reopen_and_refinish(environment):
    from test_travel_photos import upload_photo

    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    vid = (await store.list_versions(bid))[-1].id
    photo = await upload_photo(client, vid)
    view = (await client.get(f"/api/versions/{vid}/recap")).json()
    payload = {k: view[k] for k in ("title", "body", "source_fingerprint")} | {
        "body": "不覆盖我的回忆",
        "social_copy": "保留朋友圈文字",
        "photos": [{"id": photo, "included": True}],
        "cover_photo_id": photo,
    }
    saved = (await client.put(f"/api/versions/{vid}/recap", json=payload)).json()
    ended = await close(client, saved["version"]["id"])
    assert ended["itinerary"]["recap"] == saved["itinerary"]["recap"]
    end_id = ended["version"]["id"]
    recorded = await client.patch(
        f"/api/versions/{end_id}/days/2/activities/park/progress", json={"progress": "completed"}
    )
    assert recorded.status_code == 200, recorded.text
    after = recorded.json()
    assert after["itinerary"]["ended_at"] == ended["itinerary"]["ended_at"]
    items = (await client.get("/api/recap-archive")).json()["items"]
    assert (
        items[0]["completed_count"] == 2
        and items[0]["version_id"] == after["version"]["id"]
        and items[0]["stale"]
    )
    reopened = await close(client, after["version"]["id"], False)
    assert not reopened["itinerary"]["ended_at"]
    assert reopened["itinerary"]["recap"] == after["itinerary"]["recap"]
    assert (await client.get("/api/recap-archive")).json()["total"] == 0
    assert (await close(client, reopened["version"]["id"], False))["version"]["id"] == reopened[
        "version"
    ]["id"]
    stale = await client.put(f"/api/versions/{end_id}/journey-status", json={"ended": True})
    assert stale.status_code == 409
    again = await close(client, reopened["version"]["id"])
    assert (
        again["itinerary"]["ended_at"]
        and (await client.get("/api/recap-archive")).json()["total"] == 1
    )
    assert (await client.get(f"/api/versions/{end_id}/recap")).json()["ended_at"] == ended[
        "itinerary"
    ]["ended_at"]


@pytest.mark.parametrize("from_history", [False, True])
async def test_fork_reopens_only_new_branch_and_keeps_source_archived(environment, from_history):
    client, store, graph, _ = environment
    sid, bid, _ = await seed(environment)
    vid = (await store.list_versions(bid))[-1].id
    ended = await close(client, vid)
    payload = {"title": "新方案", "parent_branch_id": bid}
    if from_history:
        payload["fork_version_id"] = ended["version"]["id"]
    fork = (await client.post(f"/api/sessions/{sid}/branches", json=payload)).json()
    child = next(b for b in fork["items"] if b["title"] == "新方案")
    version = (await store.list_versions(child["id"]))[-1]
    detail = await store.get_version_detail(version.id)
    assert detail.itinerary.ended_at is None
    assert (await graph.aget_state(thread_config(child["id"]))).values["itinerary"].ended_at is None
    assert detail.itinerary.recap and (await client.get("/api/recap-archive")).json()["total"] == 1
    archived = (await client.get("/api/recap-archive")).json()["items"][0]
    assert archived["branch_id"] == bid


async def test_session_archive_does_not_end_trip_and_recap_survives_session_archive(environment):
    client, store, _, _ = environment
    sid, bid, _ = await seed(environment)
    await client.patch(f"/api/sessions/{sid}", json={"archived": True})
    assert (await client.get("/api/recap-archive")).json()["total"] == 0
    ended = await close(client, (await store.list_versions(bid))[-1].id)
    listing = (await client.get("/api/recap-archive")).json()
    assert listing["total"] == 1 and listing["items"][0]["session_archived"]
    await client.patch(f"/api/sessions/{sid}", json={"archived": False})
    assert (await client.get("/api/recap-archive")).json()["total"] == 1
    await client.delete(f"/api/sessions/{sid}")
    assert (await client.get("/api/recap-archive")).json()["total"] == 0
    assert (await client.get(f"/api/versions/{ended['version']['id']}/recap")).status_code == 400


async def test_archive_pagination_search_and_plan_only_versions_not_included(environment):
    client, store, _, _ = environment
    sid, bid, _ = await seed(environment)
    await close(client, (await store.list_versions(bid))[-1].id)
    await client.patch(f"/api/sessions/{sid}", json={"title": "北京旧时光"})
    sid2, bid2, _ = await seed(environment)
    await close(client, (await store.list_versions(bid2))[-1].id)
    await client.patch(f"/api/sessions/{sid2}", json={"title": "故宫故事"})
    await seed(environment)  # 尚未结束，不进入回顾列表。
    page1 = (await client.get("/api/recap-archive?page_size=1")).json()
    page2 = (await client.get("/api/recap-archive?page_size=1&page=2")).json()
    assert (
        page1["total"] == 2
        and page2["total"] == 2
        and len(page1["items"]) == len(page2["items"]) == 1
    )
    assert page1["items"][0]["branch_id"] != page2["items"][0]["branch_id"]
    bytitle = (await client.get("/api/recap-archive?q=旧时光")).json()
    assert bytitle["total"] == 1 and bytitle["items"][0]["session_id"] == sid
    assert (await client.get("/api/recap-archive?q=北京")).json()["total"] == 2
    assert (await client.get("/api/recap-archive?q=主方案")).json()["total"] == 2
    assert (await client.get("/api/recap-archive?q=不存在")).json()["total"] == 0
    assert (await client.get("/api/recap-archive?page=0")).status_code == 400
