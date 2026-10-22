import json
import sqlite3

import httpx
from conftest import FakeLLM
from langchain_openai import ChatOpenAI

from app.agents.checkpoint import thread_config
from app.api import routes_itinerary
from app.core.config import Settings, get_settings
from app.domain.models import Citation, Requirements
from app.llm.openai_compat import OpenAICompatProvider
from app.services.branch_lock import branch_guard
from app.storage.database import create_connection
from app.storage.session_store import SessionStore


async def generate(client):
    session = (await client.post("/api/sessions", json={"title": "北京三日游"})).json()
    sid = session["session"]["id"]
    bid = session["branches"][0]["id"]
    response = await client.post(
        "/api/chat", json={"message": "北京三日游", "session_id": sid, "branch_id": bid}
    )
    assert response.status_code == 200, response.text
    vid = response.json()["version_id"]
    detail = (await client.get(f"/api/versions/{vid}")).json()
    return sid, bid, detail


async def test_edit_preserves_other_days_updates_graph_and_exports(environment):
    client, store, graph, search = environment
    _, bid, before = await generate(client)
    assert len(before["itinerary"]["days"]) == 3
    day = before["itinerary"]["days"][1]
    old = day["activities"][0]
    activity = {**old, "title": "替换后的景点", "place_name": "新景点", "cost": 10}
    response = await client.put(
        f"/api/versions/{before['version']['id']}/days/2/activities/{old['id']}",
        json={"activity": activity},
    )
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["version"]["id"] != before["version"]["id"]
    assert after["version"]["trigger"] == "itinerary_edit"
    assert after["itinerary"]["days"][1]["known_cost"] == 20
    assert after["itinerary"]["days"][0] == before["itinerary"]["days"][0]
    assert after["itinerary"]["days"][2] == before["itinerary"]["days"][2]
    assert after["itinerary"]["days"][1]["activities"][0]["source_indices"] == []
    assert len(search.queries) == 3  # 手工编辑不会额外调用模型或搜索
    snapshot = await graph.aget_state(thread_config(bid))
    assert snapshot.values["itinerary"].days[1].known_cost == 20
    assert "替换后的景点" in snapshot.values["plan_markdown"]
    unchanged = (await client.get(f"/api/versions/{before['version']['id']}")).json()
    assert unchanged == before
    for format in ("md", "html"):
        exported = await client.get(
            f"/api/versions/{after['version']['id']}/export?format={format}"
        )
        assert exported.status_code == 200
        assert "替换后的景点" in exported.text
        assert "景点 2 A" not in exported.text
    # 旧版本不允许直接覆盖。
    rejected = await client.put(
        f"/api/versions/{before['version']['id']}/days/2/activities/{old['id']}",
        json={"activity": activity},
    )
    assert rejected.status_code == 409


async def test_fork_copies_historical_structured_state(environment):
    client, store, graph, _ = environment
    sid, bid, original = await generate(client)
    day = original["itinerary"]["days"][0]
    old = day["activities"][0]
    edited = await client.put(
        f"/api/versions/{original['version']['id']}/days/1/activities/{old['id']}",
        json={"activity": {**old, "title": "新版本", "cost": 1}},
    )
    assert edited.status_code == 200
    response = await client.post(
        f"/api/sessions/{sid}/branches",
        json={
            "title": "历史分叉",
            "parent_branch_id": bid,
            "fork_version_id": original["version"]["id"],
        },
    )
    assert response.status_code == 200, response.text
    fork = next(b for b in response.json()["items"] if b["title"] == "历史分叉")
    snapshot = await graph.aget_state(thread_config(fork["id"]))
    assert snapshot.values["itinerary"].days[0].known_cost == 120
    versions = await store.list_versions(fork["id"])
    assert len(versions) == 1
    detail = await store.get_version_detail(versions[0].id)
    assert detail.itinerary.model_dump(mode="json") == original["itinerary"]
    assert detail.citations[0].index == 1
    assert detail.requirements == Requirements(**original["requirements"])
    assert len((snapshot.values or {})["messages"]) > 0


async def test_cross_session_fork_is_rejected(environment):
    client, _, _, _ = environment
    _, bid, original = await generate(client)
    other = (await client.post("/api/sessions", json={})).json()["session"]["id"]
    response = await client.post(
        f"/api/sessions/{other}/branches",
        json={
            "title": "错误分叉",
            "parent_branch_id": bid,
            "fork_version_id": original["version"]["id"],
        },
    )
    assert response.status_code == 400


async def test_legacy_fork_does_not_copy_unrelated_latest_checkpoint(environment):
    client, store, graph, _ = environment
    sid, bid, original = await generate(client)
    legacy = await store.create_version(bid, summary="旧版本无 checkpoint")
    await store.save_plan(
        legacy.id, markdown="旧路书", citations=[Citation(**c) for c in original["citations"]]
    )
    response = await client.post(
        f"/api/sessions/{sid}/branches",
        json={
            "title": "旧版本分叉",
            "parent_branch_id": bid,
            "fork_version_id": legacy.id,
        },
    )
    assert response.status_code == 200
    fork = next(b for b in response.json()["items"] if b["title"] == "旧版本分叉")
    snapshot = await graph.aget_state(thread_config(fork["id"]))
    assert snapshot.values["plan_markdown"] == "旧路书"
    assert snapshot.values["messages"] == []
    assert snapshot.values["requirements"].destination is None
    assert len(snapshot.values["research"].build_citations()) == len(original["citations"])


async def test_route_recalculation_creates_version_and_preserves_other_days(environment):
    client, _, _, _ = environment
    _, _, before = await generate(client)
    response = await client.post(
        f"/api/versions/{before['version']['id']}/days/1/routes", json={"travel_mode": "walking"}
    )
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["itinerary"]["days"][0]["travel_mode"] == "walking"
    assert after["itinerary"]["days"][0]["travel_minutes"] is None
    assert after["itinerary"]["days"][1:] == before["itinerary"]["days"][1:]
    invalid = await client.post(
        f"/api/versions/{after['version']['id']}/days/1/routes", json={"travel_mode": "teleport"}
    )
    assert invalid.status_code == 400


async def test_branch_lock_rejects_simultaneous_chat_and_edit(environment):
    client, _, _, _ = environment
    sid, bid, before = await generate(client)
    async with branch_guard(bid):
        response = await client.post(
            f"/api/versions/{before['version']['id']}/days/1/routes",
            json={"travel_mode": "walking"},
        )
        assert response.status_code == 409
        response = await client.post(
            "/api/chat", json={"session_id": sid, "branch_id": bid, "message": "调整行程"}
        )
        assert response.status_code == 409


async def test_streamed_plan_has_structured_days_and_version(environment):
    client, _, _, _ = environment
    session = (await client.post("/api/sessions", json={})).json()
    response = await client.post(
        "/api/chat/stream", json={"session_id": session["session"]["id"], "message": "北京三日游"}
    )
    assert response.status_code == 200
    events = []
    for block in response.text.split("\n\n"):
        lines = block.splitlines()
        if len(lines) >= 2 and lines[0] == "event: done":
            events.append(json.loads(lines[1][6:]))
    assert len(events) == 1
    detail = (await client.get(f"/api/versions/{events[0]['version_id']}")).json()
    assert len(detail["itinerary"]["days"]) == 3
    assert "活动与交通费用" in events[0]["plan"]


async def test_refine_research_is_conditional_and_keeps_old_citation_ids(environment):
    client, _, _, search = environment
    sid, bid, before = await generate(client)
    await client.post(
        "/api/chat", json={"session_id": sid, "branch_id": bid, "message": "调整一下措辞"}
    )
    assert len(search.queries) == 3
    response = await client.post(
        "/api/chat", json={"session_id": sid, "branch_id": bid, "message": "换一家酒店"}
    )
    assert response.status_code == 200, response.text
    after = (await client.get(f"/api/versions/{response.json()['version_id']}")).json()
    assert len(search.queries) == 6
    assert any("换一家酒店" in query for query in search.queries)
    assert after["citations"][0]["url"] == before["citations"][0]["url"]
    assert len(after["citations"]) == 6


async def test_map_key_is_not_returned_by_settings_or_errors(environment):
    client, _, _, _ = environment
    settings = Settings(_env_file=None, amap_api_key="fake-secret-map-key")
    client._transport.app.dependency_overrides[get_settings] = lambda: settings
    response = await client.get("/api/system/settings")
    assert response.json()["amap_has_key"] is True
    assert "fake-secret-map-key" not in response.text
    assert "amap_api_key" not in response.json()


async def test_organise_legacy_plan_uses_model_and_saves_new_version(environment, monkeypatch):
    client, store, _, _ = environment
    _, bid, before = await generate(client)
    await store.save_plan(
        before["version"]["id"],
        markdown=before["markdown"],
        requirements=Requirements(**before["requirements"]),
        citations=[Citation(**c) for c in before["citations"]],
    )
    monkeypatch.setattr(routes_itinerary, "create_llm_provider", lambda settings: FakeLLM())
    response = await client.post(f"/api/versions/{before['version']['id']}/itinerary")
    assert response.status_code == 200, response.text
    assert len(response.json()["itinerary"]["days"]) == 3
    assert response.json()["version"]["id"] != before["version"]["id"]
    assert len(await store.list_versions(bid)) == 2


async def test_organise_model_auth_failure_preserves_old_plan(environment, monkeypatch):
    client, store, graph, _ = environment
    _, bid, before = await generate(client)
    versions = await store.list_versions(bid)
    snapshot = await graph.aget_state(thread_config(bid))

    def reject(request):
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": "invalid API key: fake-model-secret",
                    "type": "authentication_error",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject)) as transport:
        llm = OpenAICompatProvider(
            model="test",
            api_key="fake-model-secret",
            base_url="https://model.invalid/v1",
            max_retries=0,
        )
        monkeypatch.setattr(
            llm,
            "_build",
            lambda temperature: ChatOpenAI(
                model="test",
                api_key="fake-model-secret",
                base_url="https://model.invalid/v1",
                max_retries=0,
                http_async_client=transport,
            ),
        )
        monkeypatch.setattr(routes_itinerary, "create_llm_provider", lambda settings: llm)
        response = await client.post(f"/api/versions/{before['version']['id']}/itinerary")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LLM_AUTH_FAILED"
    assert "fake-model-secret" not in response.text
    assert len(await store.list_versions(bid)) == len(versions)
    detail = await store.get_version_detail(before["version"]["id"])
    assert detail.model_dump(mode="json") == before
    after = await graph.aget_state(thread_config(bid))
    assert after.config == snapshot.config


async def test_old_database_migrates_without_losing_plan(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE plans (version_id TEXT PRIMARY KEY, markdown TEXT NOT NULL, "
        "citations TEXT NOT NULL DEFAULT '[]', searched_at TEXT)"
    )
    old.commit()
    old.close()
    conn = await create_connection(path)
    store = SessionStore(conn)
    try:
        session = await store.create_session("旧行程")
        branch = await store.create_branch(session.id)
        version = await store.create_version(branch.id)
        await store.save_plan(version.id, markdown="旧路书")
        detail = await store.get_version_detail(version.id)
        assert detail.markdown == "旧路书"
        assert detail.itinerary is None
        assert detail.requirements is None
    finally:
        await conn.close()
