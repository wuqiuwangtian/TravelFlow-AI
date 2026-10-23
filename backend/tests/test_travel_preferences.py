import json
import sqlite3

import pytest
from conftest import FakeLLM

from app.agents.checkpoint import thread_config
from app.agents.nodes.collector import CollectResult
from app.agents.nodes.refiner import RefineIntent
from app.domain.models import Requirements
from app.storage.database import create_connection
from app.storage.session_store import SessionStore


async def saved(client):
    response = await client.get("/api/travel-preferences")
    assert response.status_code == 200
    return response.json()


async def save(client, values, revision=None):
    if revision is None:
        revision = (await saved(client))["revision"]
    return await client.put(
        "/api/travel-preferences", json={"preferences": values, "expected_revision": revision}
    )


async def create(client, *, use=False, revision=None):
    return await client.post(
        "/api/sessions", json={"use_saved_preferences": use, "preferences_revision": revision}
    )


async def test_explicit_save_normalizes_without_reading_or_learning_from_trips(environment):
    client, store, _, _ = environment
    assert await saved(client) == {"preferences": [], "revision": 0, "updated_at": None}
    response = await save(client, [" 慢节奏 ", "当地美食", "慢节奏"])
    assert response.status_code == 200
    profile = response.json()
    assert profile["preferences"] == ["慢节奏", "当地美食"] and profile["revision"] == 1
    assert profile["updated_at"]
    assert (await store.list_sessions()) == []
    # 回复丢失后使用旧修订重试相同目标，不增加修订。
    assert (await save(client, profile["preferences"], revision=0)).json() == profile
    unchanged = await create(client)
    assert unchanged.json()["session"]["initial_preferences"] == []
    assert (await saved(client)) == profile


@pytest.mark.parametrize(
    "values", [[""], ["  "], ["x" * 61], [str(i) for i in range(13)], ["别\n换行"], [42]]
)
async def test_invalid_preferences_do_not_modify_saved_profile(environment, values):
    client, _, _, _ = environment
    before = (await save(client, ["少走路"])).json()
    response = await save(client, values)
    assert response.status_code == 400
    assert (await saved(client)) == before


async def test_edit_delete_and_stale_pages_never_resurrect_or_overwrite_preferences(environment):
    client, _, _, _ = environment
    first = (await save(client, ["慢节奏"])).json()
    second = (await save(client, ["自然风光"], revision=first["revision"])).json()
    stale_save = await save(client, ["博物馆"], revision=first["revision"])
    stale_delete = await client.delete(f"/api/travel-preferences?revision={first['revision']}")
    assert stale_save.status_code == stale_delete.status_code == 409
    assert (await saved(client)) == second
    removed = await client.delete(f"/api/travel-preferences?revision={second['revision']}")
    assert removed.status_code == 200
    empty = removed.json()
    assert empty["preferences"] == [] and empty["revision"] == second["revision"] + 1
    assert (await save(client, ["自然风光"], revision=second["revision"])).status_code == 409
    assert (
        await client.delete(f"/api/travel-preferences?revision={second['revision']}")
    ).json() == empty


async def test_opt_in_requires_current_reviewed_profile_and_copies_it_at_creation(environment):
    client, _, _, _ = environment
    assert (await create(client, use=True, revision=0)).status_code == 400
    first = (await save(client, ["少走路"])).json()
    assert (await create(client, use=True)).status_code == 409
    assert (await create(client, use=True, revision=0)).status_code == 409
    selected = (await create(client, use=True, revision=first["revision"])).json()
    untouched = (await create(client)).json()
    assert selected["session"]["initial_preferences"] == ["少走路"]
    assert untouched["session"]["initial_preferences"] == []
    await save(client, ["徒步"])
    await client.delete(f"/api/travel-preferences?revision={(await saved(client))['revision']}")
    detail = (await client.get(f"/api/sessions/{selected['session']['id']}")).json()
    assert detail["session"]["initial_preferences"] == ["少走路"]
    assert len((await client.get("/api/sessions")).json()["items"]) == 2


@pytest.mark.parametrize("stream", [False, True])
async def test_first_chat_uses_creation_copy_after_profile_deleted_then_does_not_reinject(
    environment, stream
):
    client, store, graph, _ = environment
    profile = (await save(client, ["少走路", "当地美食"])).json()
    session = (await create(client, use=True, revision=profile["revision"])).json()
    sid, bid = session["session"]["id"], session["branches"][0]["id"]
    await client.delete(f"/api/travel-preferences?revision={profile['revision']}")
    path = "/api/chat/stream" if stream else "/api/chat"
    response = await client.post(
        path, json={"session_id": sid, "message": "北京三日游", "request_id": "prefs-first-request"}
    )
    assert response.status_code == 200 and (not stream or "event: done" in response.text)
    first = await store.get_version_detail((await store.list_versions(bid))[-1].id)
    assert first.requirements.preferences == ["少走路", "当地美食"]
    # 修改分支里的本次偏好后，后续对话不能再次注入初始副本。
    current = first.requirements.model_copy(update={"preferences": ["摄影"]})
    await graph.aupdate_state(thread_config(bid), {"requirements": current})
    response = await client.post(
        path, json={"session_id": sid, "message": "本次确认", "request_id": "prefs-next-request"}
    )
    assert response.status_code == 200
    state = (await graph.aget_state(thread_config(bid))).values
    assert state["requirements"].preferences == ["摄影"]
    assert (await saved(client))["preferences"] == []


async def test_current_trip_can_replace_and_clear_preferences_during_collection_and_refinement(
    environment, monkeypatch
):
    client, store, graph, _ = environment
    original = FakeLLM.structured

    async def structured(self, messages, schema, **kwargs):
        if schema is CollectResult:
            if messages[-1].content == "这次徒步，不要旧偏好":
                return CollectResult(
                    extracted=Requirements(preferences=["徒步"]), reply="按本次偏好继续"
                )
            if messages[-1].content == "这次清空偏好":
                return CollectResult(clear_preferences=True, reply="本次不沿用")
        if schema is RefineIntent and "这次清空偏好" in messages[0].content:
            return RefineIntent(intent="modify", clear_preferences=True)
        return await original(self, messages, schema, **kwargs)

    monkeypatch.setattr(FakeLLM, "structured", structured)
    profile = (await save(client, ["慢节奏"])).json()
    session = (await create(client, use=True, revision=profile["revision"])).json()
    sid, bid = session["session"]["id"], session["branches"][0]["id"]
    for message, expected in [("这次徒步，不要旧偏好", ["徒步"]), ("这次清空偏好", [])]:
        response = await client.post("/api/chat", json={"session_id": sid, "message": message})
        assert response.status_code == 200
        assert (await graph.aget_state(thread_config(bid))).values[
            "requirements"
        ].preferences == expected
    await client.post("/api/chat", json={"session_id": sid, "message": "北京三日游"})
    before = await store.get_version_detail((await store.list_versions(bid))[-1].id)
    await graph.aupdate_state(
        thread_config(bid),
        {"requirements": before.requirements.model_copy(update={"preferences": ["徒步"]})},
    )
    response = await client.post("/api/chat", json={"session_id": sid, "message": "这次清空偏好"})
    assert response.status_code == 200
    after = await store.get_version_detail((await store.list_versions(bid))[-1].id)
    assert after.requirements.preferences == []
    assert after.itinerary.days[0].activities[0].id == before.itinerary.days[0].activities[0].id
    assert (await saved(client)) == profile


async def test_old_database_migration_restart_and_preference_persistence(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as old:
        old.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY,title TEXT NOT NULL,"
            "created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
            "archived INTEGER NOT NULL DEFAULT 0)"
        )
        old.execute("INSERT INTO sessions VALUES ('old','旧旅行','2026-10-23','2026-10-23',0)")
    conn = await create_connection(path)
    store = SessionStore(conn)
    assert (await store.get_session("old")).initial_preferences == []
    await store.save_travel_preferences(["博物馆"], expected_revision=0)
    await store.create_session("选用偏好", session_id="selected", initial_preferences=["博物馆"])
    await conn.close()
    conn = await create_connection(path)
    try:
        store = SessionStore(conn)
        assert (await store.get_travel_preferences()).preferences == ["博物馆"]
        assert (await store.get_session("selected")).initial_preferences == ["博物馆"]
        # 普通对话、重启或保存会话不会自动学习偏好。
        cursor = await conn.execute("SELECT preferences FROM travel_preferences")
        assert json.loads((await cursor.fetchone())[0]) == ["博物馆"]
    finally:
        await conn.close()
