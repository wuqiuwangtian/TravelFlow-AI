from conftest import FakeLLM
from test_activity_moves import seed
from test_itinerary_api import generate
from test_journey_completion import close

from app.agents.checkpoint import thread_config
from app.agents.nodes.collector import CollectResult
from app.agents.nodes.planner import PlanOutput
from app.core.errors import LLMAuthenticationFailed
from app.domain.models import Requirements
from app.services.branch_lock import branch_guard


async def view(client, bid, vid=None):
    path = f"/api/branches/{bid}/workflow" + (f"?version_id={vid}" if vid else "")
    response = await client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


def node(result, name):
    return next(item for item in result["nodes"] if item["id"] == name)


def facts(result, name):
    return {item["label"]: item["value"] for item in node(result, name)["outputs"]}


async def test_empty_view_uses_explicit_initial_preferences_without_starting_or_persisting_graph(
    environment,
):
    client, store, graph, search = environment
    session = await store.create_session("新旅行", initial_preferences=["慢节奏"])
    branch = await store.create_branch(session.id, is_primary=True)
    before = store._conn.total_changes
    result = await view(client, branch.id)
    assert result["source"] == "empty" and result["checkpoint_status"] == "missing"
    assert result["version"] is None and result["focus_node"] == "collector"
    assert facts(result, "collector")["本次偏好"] == "慢节奏"
    assert node(result, "collector")["state"] == "needs_input"
    assert all(
        node(result, name)["state"] == "waiting" for name in ("researcher", "planner", "refiner")
    )
    assert {item["id"] for item in result["nodes"]} == {
        "collector",
        "researcher",
        "planner",
        "refiner",
    }
    assert len(result["edges"]) == 5
    assert store._conn.total_changes == before and search.queries == []
    assert (await graph.aget_state(thread_config(branch.id))).values == {}


async def test_collection_view_keeps_missing_fields_candidates_and_zero_budget_honest(
    environment, monkeypatch
):
    client, _, _, _ = environment
    original = FakeLLM.structured

    async def partial(self, messages, schema, **kwargs):
        if schema is CollectResult:
            return CollectResult(
                extracted=Requirements(origin="上海", budget=0, preferences=["摄影"]),
                reply="请补充目的地和天数",
            )
        return await original(self, messages, schema, **kwargs)

    monkeypatch.setattr(FakeLLM, "structured", partial)
    created = (await client.post("/api/sessions", json={})).json()
    sid, bid = created["session"]["id"], created["branches"][0]["id"]
    response = await client.post("/api/chat", json={"session_id": sid, "message": "从上海出发"})
    assert response.status_code == 200
    result = await view(client, bid)
    assert result["source"] == "checkpoint" and result["version"] is None
    assert facts(result, "collector")["预算"] == "¥0.00"
    assert facts(result, "collector")["仍需补充"] == "目的地、天数"
    assert node(result, "researcher")["state"] == "waiting"


async def test_generated_nodes_read_data_without_calls_writes_or_new_versions(
    environment, monkeypatch
):
    client, store, graph, search = environment
    sid, bid, detail = await generate(client)
    before = await graph.aget_state(thread_config(bid))
    writes = store._conn.total_changes
    queries = list(search.queries)

    async def forbidden(*args, **kwargs):
        raise AssertionError("查看流程不应执行图或模型")

    monkeypatch.setattr(graph, "ainvoke", forbidden)
    monkeypatch.setattr(FakeLLM, "structured", forbidden)
    for _ in range(2):
        result = await view(client, bid)
        assert result["version"]["id"] == detail["version"]["id"]
        assert (
            node(result, "researcher")["state"] == node(result, "planner")["state"] == "available"
        )
        assert facts(result, "researcher")["已保留引用"] == "3条"
        assert facts(result, "planner")["已记净消费"] == "¥0.00"
        assert int(facts(result, "planner")["预估未知项"].removesuffix("项")) > 0
        assert node(result, "refiner")["state"] == "ready"
        assert (
            next(edge for edge in result["edges"] if edge["source"] == "planner")["trigger"]
            == "next_message"
        )
    assert store._conn.total_changes == writes and search.queries == queries
    assert (await graph.aget_state(thread_config(bid))).values == before.values
    assert len(await store.list_versions(bid)) == len(await store.list_branches(sid)) == 1


async def test_historical_workflow_keeps_old_actuals_and_confirmation_without_borrowing_current(
    environment,
):
    client, store, _, _ = environment
    _, bid, say = await seed(environment)
    old = (await store.list_versions(bid))[-1].id
    await say("第1天午餐花了86.35元")
    ended = await close(client, (await store.list_versions(bid))[-1].id)
    current = await view(client, bid)
    history = await view(client, bid, old)
    assert history["historical"] and history["version"]["id"] == old
    assert facts(history, "planner")["已记净消费"] == "¥20.00"
    assert facts(current, "planner")["已记净消费"] == "¥106.35"
    assert facts(history, "refiner")["实付记录"] == "1笔"
    assert facts(current, "refiner")["实付记录"] == "2笔"
    assert facts(history, "refiner")["旅行状态"] == "尚未记录结束"
    assert facts(current, "refiner")["旅行状态"] == "已结束，仍可补记"
    assert facts(history, "refiner")["本方案保存版本"] == "1份"
    assert current["version"]["id"] == ended["version"]["id"]


async def test_foreign_versions_rejected_and_fork_view_stays_on_its_own_branch(environment):
    client, store, _, _ = environment
    sid, bid, _ = await seed(environment)
    old = (await store.list_versions(bid))[-1].id
    response = await client.post(
        f"/api/sessions/{sid}/branches", json={"title": "节点备选", "parent_branch_id": bid}
    )
    child = next(item for item in response.json()["items"] if item["title"] == "节点备选")
    result = await view(client, child["id"])
    assert result["branch"]["id"] == child["id"] and result["version"]["branch_id"] == child["id"]
    assert (
        await client.get(f"/api/branches/{child['id']}/workflow?version_id={old}")
    ).status_code == 400
    assert (await client.get("/api/branches/does-not-exist/workflow")).status_code == 404


async def test_unconfigured_runtime_can_read_legacy_plan_without_fabricated_execution(environment):
    client, store, _, _ = environment
    session = await store.create_session("旧文字方案")
    branch = await store.create_branch(session.id)
    version = await store.create_version(branch.id)
    await store.save_plan(version.id, markdown="只有文字，没有独立检索或节点运行记录")
    client._transport.app.state.graph = None
    for vid in (None, version.id):
        result = await view(client, branch.id, vid)
        assert (
            result["source"] == "saved_version" and result["checkpoint_status"] == "not_configured"
        )
        assert node(result, "researcher")["state"] == "unknown"
        assert facts(result, "planner")["结构化行程"] == "尚未整理，文字与结构化数据分开展示"
        assert facts(result, "refiner")["计划确认"] == "未保留确认状态"


async def test_missing_historical_checkpoint_never_reads_current_private_state(environment):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    legacy = await store.create_version(bid, summary="无检查点的旧版")
    await store.save_plan(
        legacy.id, markdown="旧版", requirements=Requirements(destination="旧目的地")
    )
    result = await view(client, bid, legacy.id)
    assert result["source"] == "saved_version" and result["checkpoint_status"] == "missing"
    assert facts(result, "collector")["目的地"] == "旧目的地"
    assert "已记净消费" not in facts(result, "planner")
    assert "实付记录" not in facts(result, "refiner")


async def test_unreadable_checkpoint_uses_saved_data_and_never_returns_raw_exceptions(environment):
    client, _, _, _ = environment
    _, bid, _ = await seed(environment)

    class BrokenGraph:
        async def aget_state(self, config):
            raise RuntimeError("Bearer hidden-workflow-test-secret")

    client._transport.app.state.graph = BrokenGraph()
    result = await view(client, bid)
    assert result["source"] == "saved_version" and result["checkpoint_status"] == "unavailable"
    assert "hidden-workflow-test-secret" not in str(result) and "RuntimeError" not in str(result)
    assert facts(result, "planner")["已记净消费"] == "¥20.00"


async def test_failed_generation_marks_only_real_failed_node_and_retry_clears_it(
    environment, monkeypatch
):
    client, store, _, _ = environment
    original = FakeLLM.structured

    async def fail_plan(self, messages, schema, **kwargs):
        if schema is PlanOutput:
            raise LLMAuthenticationFailed("hidden-planner-test-token")
        return await original(self, messages, schema, **kwargs)

    monkeypatch.setattr(FakeLLM, "structured", fail_plan)
    created = (await client.post("/api/sessions", json={})).json()
    sid, bid = created["session"]["id"], created["branches"][0]["id"]
    response = await client.post("/api/chat", json={"session_id": sid, "message": "北京三日游"})
    assert response.status_code == 502
    failed = await view(client, bid)
    assert node(failed, "planner")["state"] == "interrupted" and failed["focus_node"] == "planner"
    assert node(failed, "researcher")["state"] == "available"
    assert "hidden-planner-test-token" not in str(failed)
    assert await store.list_versions(bid) == []
    monkeypatch.setattr(FakeLLM, "structured", original)
    assert (
        await client.post("/api/chat", json={"session_id": sid, "message": "继续"})
    ).status_code == 200
    recovered = await view(client, bid)
    assert node(recovered, "planner")["state"] == "available"
    assert not any(item["state"] == "interrupted" for item in recovered["nodes"])


async def test_pending_operation_and_busy_branch_can_be_inspected_without_applying_it(environment):
    client, store, graph, _ = environment
    _, bid, say = await seed(environment)
    pending = await say("第2站已完成")
    assert pending["clarification"]
    before = (await graph.aget_state(thread_config(bid))).values
    count = len(await store.list_versions(bid))
    async with branch_guard(bid):
        result = await view(client, bid)
        assert result["busy"] and result["focus_node"] == "refiner"
        assert node(result, "refiner")["state"] == "needs_input"
        assert "待澄清" in facts(result, "refiner")["待确认操作"]
    assert not (await view(client, bid))["busy"]
    assert (await graph.aget_state(thread_config(bid))).values == before
    assert len(await store.list_versions(bid)) == count
