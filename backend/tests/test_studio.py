import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from test_activity_moves import seed

from app.agents.checkpoint import thread_config
from app.api import routes_studio
from app.core.config import Settings
from app.domain.workbench import StudioGraph, StudioRunInput
from app.services.studio_engine import digest, execution_order, run_graph
from app.services.studio_templates import templates
from app.storage.session_store import SessionStore


@pytest.fixture
def providers(environment, monkeypatch):
    _, _, _, search = environment
    calls = []

    class Model:
        async def chat(self, messages, **kwargs):
            calls.append((messages, kwargs))
            if "失败节点" in messages[0].content:
                raise ValueError("private-provider-token-do-not-expose")
            return "生成结果：" + digest([m.content for m in messages])[:10]

    model = Model()
    monkeypatch.setattr(routes_studio, "create_llm_provider", lambda settings: model)
    monkeypatch.setattr(routes_studio, "create_search_provider", lambda settings: search)
    return model, search, calls


def graph():
    return templates()[2].graph.model_copy(deep=True)


async def save(client, bid, workflow, revision=0):
    response = await client.put(
        f"/api/branches/{bid}/studio",
        json={"expected_revision": revision, "graph": workflow.model_dump(mode="json")},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def run(client, bid, revision=1, rid="studio-run-0001", **kwargs):
    return await client.post(
        f"/api/branches/{bid}/studio/runs",
        json={"expected_revision": revision, "request_id": rid, **kwargs},
    )


async def test_templates_have_real_branch_merge_and_typed_executable_connections(environment):
    client, _, _, _ = environment
    response = await client.get("/api/workflow/templates")
    assert {item["id"] for item in response.json()} == {
        "inspiration",
        "journey",
        "alternatives",
        "structured",
        "curated",
        "candidate_days",
        "geographic_days",
    }
    for template in templates():
        template.graph.nodes[0].parameters.text = "上海出发，北京三天，总预算2000元"
        assert len(execution_order(template.graph, None)) == len(template.graph.nodes)
    default = templates()[1].graph
    assert {e.target for e in default.edges if e.source == "places"} == {"route", "budget"}
    assert {e.source for e in default.edges if e.target == "guide"} == {"brief", "route", "budget"}


async def test_project_save_restart_and_revision_conflict_do_not_modify_journey(environment):
    client, store, runtime, _ = environment
    _, bid, _ = await seed(environment)
    checkpoint = await runtime.aget_state(thread_config(bid))
    versions = await store.list_versions(bid)
    initial = (await client.get(f"/api/branches/{bid}/studio")).json()
    assert initial["revision"] == 0 and initial["graph"] is None
    assert "expenses" not in initial["requirements_seed"]
    project = await save(client, bid, graph())
    assert project["revision"] == 1
    restarted = SessionStore(store._conn)
    assert (await restarted.workbench.project(bid)).graph == graph()
    conflict = await client.put(
        f"/api/branches/{bid}/studio",
        json={"expected_revision": 0, "graph": graph().model_dump(mode="json")},
    )
    assert conflict.status_code == 409
    assert await runtime.aget_state(thread_config(bid)) == checkpoint
    assert await store.list_versions(bid) == versions


@pytest.mark.parametrize("problem", ["cycle", "missing", "duplicate", "ports", "duplicate_edge"])
async def test_invalid_graph_is_rejected_before_save_or_external_calls(
    environment, providers, problem
):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    data = graph().model_dump(mode="json")
    if problem == "cycle":
        data["edges"].append(
            {"source": "compare", "target": "slow", "source_port": "text", "target_port": "context"}
        )
    elif problem == "missing":
        data["edges"][0]["source"] = "absent"
    elif problem == "duplicate":
        data["nodes"].append(data["nodes"][0])
    elif problem == "ports":
        data["edges"][0]["source_port"] = "sources"
    else:
        data["edges"].append(data["edges"][0])
    response = await client.put(
        f"/api/branches/{bid}/studio", json={"expected_revision": 0, "graph": data}
    )
    assert response.status_code == 400
    assert (await store.workbench.project(bid)).revision == 0
    assert providers[2] == []


async def test_execution_uses_edges_forks_merges_and_caches_only_matching_inputs(
    environment, providers
):
    client, store, runtime, _ = environment
    _, bid, _ = await seed(environment)
    checkpoint = await runtime.aget_state(thread_config(bid))
    versions = await store.list_versions(bid)
    workflow = graph()
    workflow.nodes[0].parameters.text = "北京三天，少走路，总预算2000元"
    await save(client, bid, workflow)
    first = await run(client, bid)
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["status"] == "succeeded" and len(providers[2]) == 3
    assert len(providers[1].queries) == 1
    compare = next(c for c in providers[2] if "比较两套" in c[0][0].content)
    assert "【松弛版行程 → context】" in compare[0][1].content
    assert "【丰富版行程 → context】" in compare[0][1].content
    assert "https://example.com/info" not in compare[0][1].content
    output = next(n for n in data["nodes"] if n["node_id"] == "document")["artifact"]
    assert output["sources"][0]["url"] == "https://example.com/info"
    assert (await run(client, bid)).json() == data and len(providers[2]) == 3
    cached = (await run(client, bid, rid="studio-run-0002")).json()
    assert all(n["status"] == "cached" for n in cached["nodes"])
    original_time = next(n for n in data["nodes"] if n["node_id"] == "research")["produced_at"]
    assert (
        next(n for n in cached["nodes"] if n["node_id"] == "research")["produced_at"]
        == original_time
    )
    workflow.nodes[2].parameters.prompt += "，多安排博物馆"
    await save(client, bid, workflow, 1)
    changed = (await run(client, bid, 2, "studio-run-0003")).json()
    assert len(providers[2]) == 5 and len(providers[1].queries) == 1
    statuses = {n["node_id"]: n["status"] for n in changed["nodes"]}
    assert statuses["full"] == "cached" and statuses["research"] == "cached"
    assert statuses["slow"] == "succeeded" and statuses["compare"] == "succeeded"
    # 位置只影响画布，不使生成内容失效。
    workflow.nodes[2].x += 100
    await save(client, bid, workflow, 2)
    moved = (await run(client, bid, 3, "studio-run-0004")).json()
    assert all(n["status"] == "cached" for n in moved["nodes"])
    assert await runtime.aget_state(thread_config(bid)) == checkpoint
    assert await store.list_versions(bid) == versions


async def test_selected_node_runs_only_ancestors_and_force_does_not_regenerate_siblings(
    environment, providers
):
    client, _, _, _ = environment
    _, bid, _ = await seed(environment)
    workflow = graph()
    workflow.nodes[0].parameters.text = "北京三日游"
    await save(client, bid, workflow)
    first = (await run(client, bid, target_node="slow")).json()
    assert {n["node_id"] for n in first["nodes"]} == {"brief", "research", "slow"}
    forced = (await run(client, bid, rid="studio-run-0002", target_node="slow", force=True)).json()
    assert len(providers[2]) == 2 and len(providers[1].queries) == 1
    assert {n["node_id"]: n["status"] for n in forced["nodes"]} == {
        "brief": "cached",
        "research": "cached",
        "slow": "succeeded",
    }


async def test_failed_branch_does_not_block_independent_branch_or_leak_provider_exception(
    environment, providers
):
    client, _, _, _ = environment
    _, bid, _ = await seed(environment)
    workflow = graph()
    workflow.nodes[0].parameters.text = "北京三天"
    workflow.nodes[2].parameters.prompt = "失败节点"
    await save(client, bid, workflow)
    response = await run(client, bid)
    assert response.status_code == 200 and response.json()["status"] == "failed"
    statuses = {n["node_id"]: n["status"] for n in response.json()["nodes"]}
    assert statuses["full"] == "succeeded" and statuses["slow"] == "failed"
    assert statuses["compare"] == statuses["document"] == "blocked"
    assert "private-provider-token" not in response.text


async def test_incomplete_draft_can_save_but_run_checks_inputs_before_spending(
    environment, providers
):
    client, _, _, _ = environment
    _, bid, _ = await seed(environment)
    await save(client, bid, graph())
    response = await run(client, bid)
    assert response.status_code == 400 and "还没有输入" in response.text
    assert providers[2] == [] and providers[1].queries == []


async def test_run_id_reuse_revision_conflict_unknown_branch_and_scope_are_rejected(
    environment, providers
):
    client, _, _, _ = environment
    _, bid, _ = await seed(environment)
    workflow = graph()
    workflow.nodes[0].parameters.text = "北京三天"
    await save(client, bid, workflow)
    assert (await run(client, bid, revision=2)).status_code == 409
    assert (await run(client, bid, target_node="absent")).status_code == 400
    assert (await run(client, "missing-branch")).status_code == 404
    assert (await run(client, bid)).status_code == 200
    assert (await run(client, bid, target_node="slow")).status_code == 409
    _, other, _ = await seed(environment)
    assert (await client.get(f"/api/branches/{other}/studio")).json()["runs"] == []


async def test_interrupted_run_keeps_successes_and_reuses_them_on_retry(environment, providers):
    _, store, _, _ = environment
    _, bid, _ = await seed(environment)
    workflow = graph()
    workflow.nodes[0].parameters.text = "北京三天"
    await store.workbench.save(bid, workflow, 0)
    reached = asyncio.Event()

    class SlowModel:
        async def chat(self, *args, **kwargs):
            reached.set()
            await asyncio.Event().wait()

    settings = Settings(_env_file=None, llm_api_key="test-key-not-a-real-secret", amap_api_key=None)
    task = asyncio.create_task(
        run_graph(
            bid,
            1,
            workflow,
            StudioRunInput(request_id="interrupted-run", expected_revision=1),
            store.workbench,
            settings,
            SlowModel(),
            providers[1],
        )
    )
    await asyncio.wait_for(reached.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    stopped = await store.workbench.run(bid, "interrupted-run")
    assert stopped.status == "interrupted"
    assert next(n for n in stopped.nodes if n.node_id == "research").status == "succeeded"
    retried = await run_graph(
        bid,
        1,
        workflow,
        StudioRunInput(request_id="retry-after-stop", expected_revision=1),
        store.workbench,
        settings,
        providers[0],
        providers[1],
    )
    assert retried.status == "succeeded"
    assert next(n for n in retried.nodes if n.node_id == "research").status == "cached"
    assert len(providers[1].queries) == 1


def test_import_contract_rejects_executable_fields_and_unbounded_positions():
    from pydantic import ValidationError

    data = graph().model_dump(mode="json")
    data["nodes"][0]["code"] = "do_not_execute()"
    with pytest.raises(ValidationError):
        StudioGraph.model_validate(data)
    del data["nodes"][0]["code"]
    data["nodes"][0]["x"] = float("inf")
    with pytest.raises(ValidationError):
        StudioGraph.model_validate(data)


async def test_stop_run_keeps_finished_artifacts_and_allows_subsequent_resume(
    environment, providers, monkeypatch
):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    workflow = graph()
    workflow.nodes[0].parameters.text = "北京三天"
    await save(client, bid, workflow)
    reached = asyncio.Event()

    class SlowModel:
        async def chat(self, *args, **kwargs):
            reached.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(routes_studio, "create_llm_provider", lambda settings: SlowModel())
    pending = asyncio.create_task(run(client, bid))
    await asyncio.wait_for(reached.wait(), 3)
    stopped = await client.post(f"/api/branches/{bid}/studio/runs/studio-run-0001/stop")
    assert stopped.status_code == 200
    response = await asyncio.wait_for(pending, 3)
    assert response.status_code == 200 and response.json()["status"] == "interrupted"
    assert (await store.workbench.run(bid, "studio-run-0001")).status == "interrupted"
    assert (bid, "studio-run-0001") not in routes_studio._running
    monkeypatch.setattr(routes_studio, "create_llm_provider", lambda settings: providers[0])
    resumed = await run(client, bid, rid="studio-resumed-0002")
    assert resumed.json()["status"] == "succeeded" and len(providers[1].queries) == 1


async def test_expired_search_cache_refreshes_sources_without_recomputing_unchanged_content(
    environment, providers
):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    workflow = graph()
    workflow.nodes[0].parameters.text = "北京三天"
    await save(client, bid, workflow)
    assert (await run(client, bid)).json()["status"] == "succeeded"
    previous = await store.workbench.run(bid, "studio-run-0001")
    next(n for n in previous.nodes if n.node_id == "research").produced_at = datetime.now(
        UTC
    ) - timedelta(days=2)
    await store.workbench.write_run(previous)
    result = await run(client, bid, rid="refresh-expired-sources")
    assert result.json()["status"] == "succeeded"
    assert (
        next(n for n in result.json()["nodes"] if n["node_id"] == "research")["status"]
        == "succeeded"
    )
    assert len(providers[1].queries) == 2 and len(providers[2]) == 3


async def test_model_config_invalidates_only_generation_without_exposing_keys(
    environment, providers
):
    _, store, _, _ = environment
    _, bid, _ = await seed(environment)
    workflow = graph()
    workflow.nodes[0].parameters.text = "北京三天"
    await store.workbench.save(bid, workflow, 0)
    settings = Settings(
        _env_file=None, llm_api_key="studio-fake-private-config-value", amap_api_key=None
    )
    first = await run_graph(
        bid,
        1,
        workflow,
        StudioRunInput(request_id="config-run-0001", expected_revision=1),
        store.workbench,
        settings,
        providers[0],
        providers[1],
    )
    assert "studio-fake-private-config-value" not in first.model_dump_json()
    changed = settings.model_copy(update={"llm_model": "another-test-model"})
    second = await run_graph(
        bid,
        1,
        workflow,
        StudioRunInput(request_id="config-run-0002", expected_revision=1),
        store.workbench,
        changed,
        providers[0],
        providers[1],
    )
    assert next(n for n in second.nodes if n.node_id == "research").status == "cached"
    assert len(providers[1].queries) == 1 and len(providers[2]) == 6
