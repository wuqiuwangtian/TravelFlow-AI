from datetime import UTC, datetime

import pytest
from adoption_fixtures import adoption_draft
from conftest import FakeLLM
from test_activity_moves import seed
from test_journey_completion import close

from app.agents.checkpoint import thread_config
from app.api import routes_itinerary, routes_studio
from app.core.errors import LLMAuthenticationFailed
from app.domain.itinerary import (
    Activity,
    BudgetItem,
    ExpenseAdjustment,
    RecapPhoto,
    RouteLeg,
    TravelRecap,
)
from app.domain.models import ResearchBundle
from app.domain.studio_adoption import StudioPlanDraft
from app.domain.studio_data import StudioPlanData
from app.domain.workbench import NodeArtifact, NodeExecution, StudioRun, StudioSource
from app.search.base import SearchResult
from app.services.branch_lock import branch_guard
from app.services.itinerary_service import build_itinerary
from app.services.studio_templates import templates


async def latest(store, bid):
    versions = await store.list_versions(bid)
    return await store.get_version_detail(versions[-1].id) if versions else None


async def source(store, bid, *, rid="adopt-source-0001", node="document"):
    graph = templates()[1].graph
    graph.nodes[0].parameters.text = "上海出发北京三天，全团预算2000元，两人"
    run = StudioRun(
        id=rid,
        branch_id=bid,
        revision=1,
        graph=graph,
        target_node=None,
        force=False,
        status="succeeded",
        created_at=datetime.now(UTC),
        nodes=[
            NodeExecution(
                node_id=node,
                status="succeeded",
                input_key="input-key",
                artifact=NodeArtifact(
                    text="北京三日每日行程，门票依据[1]。",
                    sources=[
                        StudioSource(
                            title="新成稿资料",
                            url="https://example.com/new",
                            snippet="门票80元",
                            provider="fake",
                        )
                    ],
                ),
                produced_at=datetime.now(UTC),
            )
        ],
    )
    await store.workbench.write_run(run)
    return run


def path(bid, rid="adopt-source-0001", node="document"):
    return f"/api/branches/{bid}/studio/runs/{rid}/nodes/{node}/adoption"


async def preview(
    client,
    bid,
    *,
    rid="adopt-source-0001",
    node="document",
    request_id="adopt-request-0001",
    context=None,
):
    context = context or (await client.get(path(bid, rid, node))).json()
    return await client.post(
        path(bid, rid, node),
        json={
            "request_id": request_id,
            "base_key": context["base_key"],
            "source_key": context["source_key"],
        },
    )


async def apply(
    client, bid, request_id="adopt-request-0001", rid="adopt-source-0001", node="document"
):
    return await client.post(path(bid, rid, node) + f"/{request_id}/apply")


@pytest.fixture
def model(monkeypatch):
    calls = []
    draft = adoption_draft()

    class Model(FakeLLM):
        async def structured(self, messages, schema, **kwargs):
            assert schema is StudioPlanDraft
            calls.append(messages)
            return draft.model_copy(deep=True)

    monkeypatch.setattr(routes_studio, "create_llm_provider", lambda settings: Model())
    return draft, calls


async def test_preview_is_persistent_model_retry_reuses_result_and_does_not_change_plan_or_graph(
    environment, model
):
    client, store, graph, _ = environment
    _, bid, _ = await seed(environment)
    await source(store, bid)
    before = await latest(store, bid)
    checkpoint = await graph.aget_state(thread_config(bid))
    result = await preview(client, bid)
    assert result.status_code == 200, result.text
    data = result.json()
    assert not data["conflicts"], data["conflicts"]
    assert data["base_version_id"] == before.version.id
    assert {c["status"] for c in data["changes"]} >= {"added", "removed", "preserved"}
    assert (await latest(store, bid)) == before
    assert (await graph.aget_state(thread_config(bid))) == checkpoint
    assert (await client.get(path(bid))).json()["preview"] == data
    assert (await preview(client, bid)).json() == data and len(model[1]) == 1
    assert (await client.delete(path(bid) + "/adopt-request-0001")).status_code == 204
    assert (await client.get(path(bid))).json()["preview"] is None
    assert (await apply(client, bid)).status_code == 400


async def test_apply_retains_records_links_manual_budget_photos_history_and_updates_chat_state(
    environment, model
):
    client, store, graph, _ = environment
    _, bid, say = await seed(environment)
    original = await latest(store, bid)
    plan = original.itinerary
    plan.expenses[0].notes = "私密付款备注不得发给模型"
    plan.expenses[0].receipt_ids = ["a" * 32]
    plan.adjustments.append(
        ExpenseAdjustment(
            id="refund",
            expense_id=plan.expenses[0].id,
            amount_cents=500,
            kind="refund",
            status="received",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
    )
    plan.budget_items.append(
        BudgetItem(
            id="meal-extra", title="人工餐饮备用", planned_cents=12345, category="meal", day=3
        )
    )
    plan.recap = TravelRecap(
        title="私密回顾标题",
        body="私密回顾正文",
        source_fingerprint="old",
        photos=[RecapPhoto(id="b" * 32, activity_id="park", caption="私密照片说明", included=True)],
        cover_photo_id="b" * 32,
    )
    await routes_itinerary._save_revision(store, graph, original, "准备保护记录")
    before = await latest(store, bid)
    await source(store, bid)
    result = await preview(client, bid)
    assert result.status_code == 200 and not result.json()["conflicts"], result.text
    prompt = " ".join(m.content for m in model[1][0])
    assert all(value not in prompt for value in ("私密付款备注", "私密回顾", "私密照片", "a" * 32))
    saved = await apply(client, bid)
    assert saved.status_code == 200, saved.text
    adopted = await latest(store, bid)
    new = adopted.itinerary
    assert new.expenses == before.itinerary.expenses
    assert new.adjustments == before.itinerary.adjustments
    assert new.budget_items == before.itinerary.budget_items and new.recap == before.itinerary.recap
    protected = {"past", "station", "hotel-one", "park"}
    before_by_id = {a.id: a for day in before.itinerary.days for a in day.activities}
    after_by_id = {a.id: a for day in new.days for a in day.activities}
    assert all(after_by_id[id] == before_by_id[id] for id in protected)
    assert next(a for a in new.days[1].activities if a.id == "park").cost == 20
    assert adopted.citations[0].url == "https://example.com/new"
    assert (await graph.aget_state(thread_config(bid))).values["itinerary"] == new
    assert await store.get_version_detail(before.version.id) == before
    assert (await client.get(path(bid))).json()["preview"] is None
    await say("第3天午餐花了12元")
    count = len(await store.list_versions(bid))
    replay = await apply(client, bid)
    assert replay.json() == saved.json() and len(await store.list_versions(bid)) == count
    assert len((await latest(store, bid)).itinerary.expenses) == 2


async def test_first_plan_can_be_adopted_without_existing_version_or_prior_chat(environment, model):
    client, store, graph, _ = environment
    session = await store.create_session("工程直接成稿")
    branch = await store.create_branch(session.id, is_primary=True)
    await source(store, branch.id)
    result = await preview(client, branch.id)
    assert result.status_code == 200 and result.json()["base_version_id"] is None
    assert not result.json()["conflicts"], result.text
    assert (await apply(client, branch.id)).status_code == 200
    detail = await latest(store, branch.id)
    assert detail.version.seq == 1 and detail.requirements.is_complete
    assert len(detail.itinerary.days) == 3
    assert (await graph.aget_state(thread_config(branch.id))).values["status"] == "reviewing"


async def test_new_payment_or_checkpoint_only_change_rejects_stale_preview(environment, model):
    client, store, graph, _ = environment
    _, bid, say = await seed(environment)
    await source(store, bid)
    old_context = (await client.get(path(bid))).json()
    assert (await preview(client, bid)).status_code == 200
    await say("第3天午餐花了38元")
    newest = await latest(store, bid)
    assert (await apply(client, bid)).status_code == 409
    assert (await preview(client, bid, context=old_context)).status_code == 409
    assert (await client.get(path(bid))).json()["preview"] is None
    assert await latest(store, bid) == newest
    assert (await preview(client, bid, request_id="adopt-request-0002")).status_code == 200
    await graph.aupdate_state(thread_config(bid), {"updated_at": datetime.now()}, as_node="refiner")
    assert (await apply(client, bid, "adopt-request-0002")).status_code == 409


@pytest.mark.parametrize(
    "problem", ["not_itinerary", "missing_day", "duration", "travelers", "unknown_place", "booking"]
)
async def test_nonadoptable_or_conflicting_plans_never_overwrite_existing_records(
    environment, model, problem
):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment, booked=problem == "booking")
    await source(store, bid)
    draft = model[0]
    if problem == "not_itinerary":
        draft.adoptable, draft.reason = False, "有多个未决备选"
    elif problem == "missing_day":
        draft.days.pop()
    elif problem == "duration":
        draft.requirements.duration_days = 2
    elif problem == "travelers":
        draft.requirements.travelers = 8  # seed旅客人数为空时不会改写，先设置原人数。
    elif problem == "unknown_place":
        draft.days[2].activities[0].place_name = ""
    if problem == "travelers":
        detail = await latest(store, bid)
        detail.requirements.travelers = 2
        await routes_itinerary._save_revision(store, environment[2], detail, "明确人数")
    if problem == "booking":
        detail = await latest(store, bid)
        detail.itinerary.days[1].activities[1].transport_minutes = None
        await routes_itinerary._save_revision(store, environment[2], detail, "预约前交通未知")
    before = await latest(store, bid)
    response = await preview(client, bid)
    if problem == "not_itinerary":
        assert response.status_code == 400
    else:
        assert response.status_code == 200 and response.json()["conflicts"], response.text
        assert (await apply(client, bid)).status_code == 409
    assert await latest(store, bid) == before


async def test_pending_operations_ended_trip_and_branch_lock_block_adoption(environment, model):
    client, store, _, _ = environment
    _, bid, say = await seed(environment)
    await source(store, bid)
    context = (await client.get(path(bid))).json()
    async with branch_guard(bid):
        assert (await preview(client, bid, context=context)).status_code == 409
    await say("第2站已完成")
    assert "待确认" in (await client.get(path(bid))).json()["blocker"]
    assert (await preview(client, bid)).status_code == 409
    await say("取消")
    ended = await close(client, (await latest(store, bid)).version.id)
    assert ended["itinerary"]["ended_at"]
    assert (await preview(client, bid)).status_code == 409
    assert model[1] == []


async def test_source_scope_snapshot_integrity_and_request_id_binding(environment, model):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    _, other, _ = await seed(environment)
    run = await source(store, bid)
    assert (await client.get(path(other))).status_code == 400
    assert (await client.get(path(bid, node="route"))).status_code == 400
    assert (await preview(client, bid)).status_code == 200
    await source(store, bid, rid="adopt-source-0002")
    assert (await preview(client, bid, rid="adopt-source-0002")).status_code == 409
    assert (await apply(client, bid, rid="adopt-source-0002")).status_code == 400
    run.nodes[0].artifact.text += "被替换的来源"
    await store.workbench.write_run(run)
    assert (await apply(client, bid)).status_code == 409


async def test_model_failure_keeps_graph_and_versions_unchanged(environment, monkeypatch):
    client, store, graph, _ = environment
    _, bid, _ = await seed(environment)
    await source(store, bid)
    before = await graph.aget_state(thread_config(bid))
    detail = await latest(store, bid)

    class Broken:
        async def structured(self, *args):
            raise LLMAuthenticationFailed()

    monkeypatch.setattr(routes_studio, "create_llm_provider", lambda settings: Broken())
    result = await preview(client, bid)
    assert result.status_code == 502 and result.json()["error"]["code"] == "LLM_AUTH_FAILED"
    assert await store.workbench.adoption(bid) is None
    assert await graph.aget_state(thread_config(bid)) == before
    assert await latest(store, bid) == detail


async def test_old_and_new_references_are_remapped_and_remain_available_to_next_chat(
    environment, model
):
    client, store, graph, _ = environment
    _, bid, say = await seed(environment)
    detail = await latest(store, bid)
    old = ResearchBundle(
        transport=[
            SearchResult(
                title="原资料", url="https://example.com/old", content="原票价", provider="fake"
            )
        ]
    )
    detail.itinerary.days[1].activities[0].source_indices = [1]
    await graph.aupdate_state(thread_config(bid), {"research": old}, as_node="refiner")
    detail.citations = old.build_citations()
    await routes_itinerary._save_revision(store, graph, detail, "原引用资料")
    await source(store, bid)
    result = await preview(client, bid)
    assert result.status_code == 200 and not result.json()["conflicts"], result.text
    saved = await apply(client, bid)
    assert saved.status_code == 200
    data = saved.json()
    assert [c["url"] for c in data["citations"]] == [
        "https://example.com/old",
        "https://example.com/new",
    ]
    park = next(a for a in data["itinerary"]["days"][1]["activities"] if a["id"] == "park")
    assert park["source_indices"] == [1]
    assert data["itinerary"]["days"][2]["activities"][0]["source_indices"] == [2]
    assert "[2]" in data["markdown"]
    await say("第3天午餐花了12元")
    assert [c.url for c in (await latest(store, bid)).citations] == [
        "https://example.com/old",
        "https://example.com/new",
    ]


async def test_manual_transport_link_keeps_endpoints_and_rejects_splitting_existing_paid_leg(
    environment, model
):
    client, store, graph, _ = environment
    _, bid, _ = await seed(environment)
    detail = await latest(store, bid)
    detail.itinerary.budget_items.append(
        BudgetItem(
            id="leg-museum",
            title="人工交通",
            planned_cents=900,
            category="transport",
            day=2,
            activity_id="museum",
            source="transport",
        )
    )
    detail.itinerary.days[1].legs = [
        RouteLeg(
            from_id="park",
            to_id="museum",
            mode="transit",
            status="planned",
            duration_seconds=1800,
            cost=9,
            cost_basis="total",
            cost_source="manual",
        )
    ]
    await routes_itinerary._save_revision(store, graph, detail, "人工交通关联")
    await source(store, bid)
    result = await preview(client, bid)
    assert result.status_code == 200 and not result.json()["conflicts"], result.text
    row = next(r for r in result.json()["budget_after"]["rows"] if r["id"] == "leg-museum")
    assert row["planned_cents"] == 900 and row["activity_id"] == "museum"
    assert result.json()["itinerary"]["days"][1]["legs"][0]["cost_source"] == "manual"
    model[0].days[1].activities.append(
        Activity(
            title="新停留",
            place_name="新公园",
            city="北京",
            start_time="10:00",
            duration_minutes=30,
        )
    )
    split = await preview(client, bid, request_id="adopt-split-0002")
    assert split.status_code == 200 and any("人工交通" in c for c in split.json()["conflicts"])
    assert (await apply(client, bid, "adopt-split-0002")).status_code == 409


async def test_ambiguous_duplicate_protected_place_blocks_unsafe_identity_mapping(
    environment, model
):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    await source(store, bid)
    other = model[0].days[1].activities[0].model_copy(deep=True)
    other.title, other.start_time = "第二次公园停留", "15:00"
    model[0].days[1].activities.append(other)
    response = await preview(client, bid)
    assert response.status_code == 200 and any(
        "重复停留" in c for c in response.json()["conflicts"]
    )
    assert (await apply(client, bid)).status_code == 409


async def test_bookings_remain_exact_when_arrival_is_known_and_applied_receipt_survives_discard(
    environment, model
):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment, booked=True)
    before = await latest(store, bid)
    await source(store, bid)
    result = await preview(client, bid)
    assert result.status_code == 200 and not result.json()["conflicts"], result.text
    adopted = await apply(client, bid)
    assert adopted.status_code == 200
    museum = next(
        a for a in adopted.json()["itinerary"]["days"][1]["activities"] if a["id"] == "museum"
    )
    assert museum == before.itinerary.days[1].activities[1].model_dump(mode="json")
    await client.delete(path(bid) + "/adopt-request-0001")
    replay = await apply(client, bid)
    assert replay.json() == adopted.json()


async def test_structured_adoption_rebuilds_text_without_stale_source_budget(
    environment, monkeypatch
):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    before = await latest(store, bid)
    run = await source(store, bid)
    draft = adoption_draft()
    artifact = run.nodes[0].artifact
    assert artifact is not None
    artifact.plan = StudioPlanData(
        requirements=draft.requirements,
        days=build_itinerary(draft.days, draft.requirements).days,
    )
    artifact.text = "# 原稿旧日程\n\n原稿旧预算999元，全团小计1079元。\n"
    await store.workbench.write_run(run)

    def unexpected_model(settings):
        raise AssertionError("已有结构化数据不应再次提取")

    monkeypatch.setattr(routes_studio, "create_llm_provider", unexpected_model)
    response = await preview(client, bid)
    assert response.status_code == 200, response.text
    data = response.json()
    assert not data["conflicts"]
    assert "999" not in data["markdown"] and "1079" not in data["markdown"]
    assert "原稿旧" not in data["markdown"]
    assert data["markdown"].count("## 四、每日详细行程") == 1
    assert data["markdown"].count("## 五、费用预估") == 1
    assert "北京" in data["markdown"]
    adopted = await apply(client, bid)
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["markdown"] == data["markdown"]
    assert (
        adopted.json()["itinerary"]["expenses"]
        == before.itinerary.model_dump(mode="json")["expenses"]
    )
    assert (await store.get_version_detail(before.version.id)) == before
    saved_source = await store.workbench.run(bid, run.id)
    assert "999" in saved_source.nodes[0].artifact.text
