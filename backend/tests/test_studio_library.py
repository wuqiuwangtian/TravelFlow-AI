import asyncio

import pytest
from test_activity_moves import seed
from test_studio_adoption import latest

from app.agents.checkpoint import thread_config
from app.api import routes_itinerary, routes_studio
from app.domain.workbench import StudioNode
from app.services.studio_templates import templates
from app.storage.database import create_connection
from app.storage.session_store import SessionStore

BASE = "/api/workflow/user-templates"


def payload(**updates):
    graph = templates()[3].graph.model_copy(deep=True)
    graph.title = "来源旅行的私人名称"
    graph.nodes[0].parameters.text = "来源旅行的私人需求"
    graph.nodes.append(
        StudioNode(
            id="constraints",
            kind="input",
            title="通用约束",
            parameters={"text": "午后留一小时休息"},
        )
    )
    graph.nodes[2].parameters.prompt = "少折返，保留编号资料，先安排预约景点。"
    return {
        "request_id": "personal-template-0001",
        "expected_revision": 0,
        "title": "博物馆慢游",
        "description": "适合少赶路的安排",
        "graph": graph.model_dump(mode="json"),
        **updates,
    }


async def create(client, **updates):
    response = await client.post(BASE, json=payload(**updates))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("模板操作不应创建外部服务")

    monkeypatch.setattr(routes_studio, "create_llm_provider", unexpected)
    monkeypatch.setattr(routes_studio, "create_search_provider", unexpected)


async def test_template_save_clears_trip_inputs_preserves_explicit_constants_and_survives_restart(
    environment,
):
    client, store, runtime, _ = environment
    _, bid, _ = await seed(environment)
    before = await latest(store, bid)
    checkpoint = await runtime.aget_state(thread_config(bid))
    body = payload(trip_input_ids=["brief"])
    response = await client.post(BASE, json=body)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["revision"] == 1 and saved["node_count"] == 7
    assert saved["graph"]["title"] == saved["title"]
    nodes = saved["graph"]["nodes"]
    assert nodes[0]["parameters"]["text"] == ""
    assert nodes[-1]["parameters"]["text"] == "午后留一小时休息"
    assert nodes[2]["parameters"]["prompt"] == body["graph"]["nodes"][2]["parameters"]["prompt"]
    assert "来源旅行的私人" not in response.text
    assert "runs" not in saved and "branch_id" not in saved
    summary = (await client.get(BASE)).json()[0]
    assert "graph" not in summary and "trip_input_ids" not in summary
    assert summary["trip_input_count"] == 1
    assert (await client.post(BASE, json=body)).json() == saved
    restarted = SessionStore(store._conn)
    assert (await restarted.studio_library.get(saved["id"])).model_dump(mode="json") == saved
    assert await latest(store, bid) == before
    assert await runtime.aget_state(thread_config(bid)) == checkpoint
    assert (await store.workbench.project(bid)).graph is None


async def test_default_binding_clears_all_inputs_and_source_deletion_does_not_remove_template(
    environment,
):
    client, store, _, _ = environment
    sid, _, _ = await seed(environment)
    saved = await create(client)
    assert saved["trip_input_ids"] == ["brief", "constraints"]
    assert all(not n["parameters"]["text"] for n in saved["graph"]["nodes"])
    assert (await client.delete(f"/api/sessions/{sid}")).status_code == 200
    assert (await client.get(BASE + "/" + saved["id"])).json() == saved
    assert len(await store.studio_library.list()) == 1


async def test_loading_uses_latest_target_requirements_and_only_returns_unsaved_graph(environment):
    client, store, runtime, _ = environment
    _, source_bid, _ = await seed(environment)
    _, target_bid, _ = await seed(environment)
    saved = await create(client, trip_input_ids=["brief"])
    detail = await latest(store, target_bid)
    detail.requirements.destination = "杭州"
    detail.requirements.origin = "广州"
    detail.requirements.preferences = ["这次想逛美术馆"]
    await routes_itinerary._save_revision(store, runtime, detail, "准备目标需求")
    before = await latest(store, target_bid)
    checkpoint = await runtime.aget_state(thread_config(target_bid))
    response = await client.post(
        f"/api/branches/{target_bid}/studio/template-drafts/{saved['id']}",
        json={"expected_revision": 1},
    )
    assert response.status_code == 200, response.text
    loaded = response.json()
    assert "杭州" in loaded["nodes"][0]["parameters"]["text"]
    assert "广州" in loaded["nodes"][0]["parameters"]["text"]
    assert "这次想逛美术馆" in loaded["nodes"][0]["parameters"]["text"]
    assert loaded["nodes"][-1]["parameters"]["text"] == "午后留一小时休息"
    assert (await client.get(BASE + "/" + saved["id"])).json() == saved
    assert await latest(store, target_bid) == before
    assert await runtime.aget_state(thread_config(target_bid)) == checkpoint
    assert (await store.workbench.project(target_bid)).revision == 0
    assert (await store.workbench.project(source_bid)).revision == 0
    session = (await client.post("/api/sessions", json={})).json()
    bid = session["branches"][0]["id"]
    empty = (
        await client.post(
            f"/api/branches/{bid}/studio/template-drafts/{saved['id']}",
            json={"expected_revision": 1},
        )
    ).json()
    assert empty["nodes"][0]["parameters"]["text"] == ""
    assert empty["nodes"][-1]["parameters"]["text"] == "午后留一小时休息"


async def test_revision_checks_retry_and_name_changes_do_not_overwrite_loaded_graphs(environment):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    saved = await create(client)
    await store.workbench.save(bid, templates()[0].graph, 0)
    project = await store.workbench.project(bid)
    changed = payload(
        request_id="personal-update-0001",
        expected_revision=1,
        title="雨天美术馆",
        trip_input_ids=saved["trip_input_ids"],
    )
    path = BASE + "/" + saved["id"]
    response = await client.put(path, json=changed)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["revision"] == 2 and updated["created_at"] == saved["created_at"]
    assert (await client.put(path, json=changed)).json() == updated
    assert (await client.put(path, json={**changed, "title": "同请求改内容"})).status_code == 409
    assert (
        await client.put(path, json={**changed, "request_id": "personal-stale-0001"})
    ).status_code == 409
    assert (await client.post(BASE, json=payload())).status_code == 409
    assert (
        await client.post(
            f"/api/branches/{bid}/studio/template-drafts/{saved['id']}",
            json={"expected_revision": 1},
        )
    ).status_code == 409
    assert await store.workbench.project(bid) == project


async def test_delete_is_retryable_erases_contents_and_old_requests_cannot_resurrect(environment):
    client, store, _, _ = environment
    saved = await create(client, trip_input_ids=[])
    path = BASE + "/" + saved["id"]
    request = {"request_id": "personal-delete-0001", "expected_revision": 1}
    assert (
        await client.request("DELETE", path, json={**request, "expected_revision": 2})
    ).status_code == 409
    assert (await client.request("DELETE", path, json=request)).status_code == 204
    assert (await client.request("DELETE", path, json=request)).status_code == 204
    assert (await client.get(path)).status_code == 404
    assert (await client.get(BASE)).json() == []
    row = await store.studio_library.row(saved["id"])
    assert (
        row["revision"] == 2 and row["graph"] == "{}" and row["title"] == row["description"] == ""
    )
    assert (await client.post(BASE, json=payload())).status_code == 409
    assert (
        await client.put(
            path, json=payload(request_id="old-update-after-delete", expected_revision=1)
        )
    ).status_code == 404
    replacement = await create(client, request_id="personal-template-0002")
    assert replacement["title"] == saved["title"]


@pytest.mark.parametrize(
    "problem", ["cycle", "binding_node", "binding_type", "duplicate_binding", "empty", "artifact"]
)
async def test_invalid_template_never_persists_or_runs(environment, problem):
    client, store, _, _ = environment
    body = payload()
    if problem == "cycle":
        body["graph"]["nodes"].append({"id": "feedback", "kind": "generate", "title": "回路反馈"})
        body["graph"]["edges"].extend(
            [
                {
                    "source": "plan",
                    "target": "feedback",
                    "source_port": "itinerary",
                    "target_port": "itinerary",
                },
                {
                    "source": "feedback",
                    "target": "plan",
                    "source_port": "text",
                    "target_port": "context",
                },
            ]
        )
    elif problem == "binding_node":
        body["trip_input_ids"] = ["absent"]
    elif problem == "binding_type":
        body["trip_input_ids"] = ["route"]
    elif problem == "duplicate_binding":
        body["trip_input_ids"] = ["brief", "brief"]
    elif problem == "empty":
        body["graph"]["nodes"] = []
        body["graph"]["edges"] = []
    else:
        body["graph"]["artifact"] = {"text": "个人运行结果"}
    response = await client.post(BASE, json=body)
    assert response.status_code in {400, 422}
    assert await store.studio_library.list() == []


async def test_normalized_name_uniqueness_blank_validation_and_failed_update_preserve_original(
    environment,
):
    client, _, _, _ = environment
    first = await create(client, title="　Slow   Travel　")
    assert (
        await client.post(
            BASE, json=payload(request_id="personal-template-0002", title="slow travel")
        )
    ).status_code == 409
    second = await create(client, request_id="personal-template-0003", title="雨天模板")
    failed = await client.put(
        BASE + "/" + second["id"],
        json=payload(request_id="personal-update-0001", expected_revision=1, title="SLOW TRAVEL"),
    )
    assert failed.status_code == 409
    assert (await client.get(BASE + "/" + second["id"])).json() == second
    assert (await client.get(BASE + "/" + first["id"])).json() == first
    assert (
        await client.post(BASE, json=payload(request_id="personal-template-0004", title="   "))
    ).status_code == 400


async def test_concurrent_updates_cannot_both_win_and_incomplete_templates_can_save(environment):
    client, _, _, _ = environment
    saved = await create(client)
    path = BASE + "/" + saved["id"]
    responses = await asyncio.gather(
        *(
            client.put(
                path,
                json=payload(
                    request_id=f"personal-update-000{i}", expected_revision=1, title=f"模板{i}"
                ),
            )
            for i in range(2)
        )
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    body = payload(request_id="personal-template-0009", title="未完成的工程")
    body["graph"]["edges"] = []
    assert (await client.post(BASE, json=body)).status_code == 200


async def test_template_limit_and_schema_reopen_are_safe(environment, tmp_path):
    client, store, _, _ = environment
    for i in range(100):
        await create(client, request_id=f"personal-template-{i:04d}", title=f"模板{i}")
    response = await client.post(
        BASE, json=payload(request_id="personal-template-0101", title="第101个")
    )
    assert response.status_code == 400 and "100" in response.text
    assert len(await store.studio_library.list()) == 100
    # 同一数据库二次启动不清理已有个人模板。
    db = tmp_path / "test.db"
    reopened = await create_connection(db)
    try:
        assert len(await SessionStore(reopened).studio_library.list()) == 100
    finally:
        await reopened.close()


async def test_different_template_creates_can_queue_without_false_conflicts(environment):
    client, store, _, _ = environment
    responses = await asyncio.gather(
        *(
            client.post(
                BASE, json=payload(request_id=f"independent-create-{i:04d}", title=f"独立模板{i}")
            )
            for i in range(3)
        )
    )
    assert all(response.status_code == 200 for response in responses)
    assert len(await store.studio_library.list()) == 3
