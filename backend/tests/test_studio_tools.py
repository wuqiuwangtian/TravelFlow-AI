import pytest
from conftest import FakeLLM, requirements
from test_activity_moves import seed
from test_studio import run, save
from test_studio_adoption import apply, latest, preview

from app.agents.checkpoint import thread_config
from app.api import routes_studio
from app.core.config import Settings, get_settings
from app.core.errors import InvalidRequest, ProviderError
from app.domain.itinerary import Activity, DayDraft, Place, RouteLeg
from app.domain.studio_adoption import StudioPlanDraft
from app.domain.workbench import NodeArtifact, StudioEdge, StudioSource
from app.services import studio_tools
from app.services.studio_engine import execute_node, provider_key
from app.services.studio_templates import templates
from app.services.studio_tools import (
    calculate_budget,
    generate_plan,
    project_artifact,
    validate_plan,
)


def draft():
    return StudioPlanDraft(
        adoptable=True,
        requirements=requirements().model_copy(update={"duration_days": 1}),
        days=[
            DayDraft(
                day=1,
                title="北京漫步",
                activities=[
                    Activity(
                        title="游览故宫",
                        place_name="故宫博物院",
                        city="北京",
                        kind="attraction",
                        start_time="09:00",
                        duration_minutes=60,
                        cost=60,
                        cost_basis="per_person",
                        cost_source="manual",
                        source_indices=[1, 99],
                    ),
                    Activity(
                        title="登景山",
                        place_name="景山公园",
                        city="北京",
                        kind="attraction",
                        start_time="11:00",
                        duration_minutes=60,
                        cost=10,
                        cost_basis="unknown",
                    ),
                    Activity(
                        title="北海散步",
                        place_name="北海公园",
                        city="北京",
                        kind="attraction",
                        start_time="13:00",
                        duration_minutes=60,
                        cost=0,
                        cost_basis="total",
                    ),
                ],
            )
        ],
    )


@pytest.fixture
def providers(environment, monkeypatch):
    calls, maps = [], []
    result = draft()

    class Model(FakeLLM):
        async def structured(self, messages, schema, **kwargs):
            assert schema is StudioPlanDraft
            calls.append(messages)
            return result.model_copy(deep=True)

    class Maps:
        async def search_places(self, keyword, city):
            maps.append((keyword, city))
            return [
                Place(
                    id=keyword,
                    name=keyword,
                    city=city,
                    longitude=116.39,
                    latitude=39.9,
                    typecode="110000",
                )
            ]

        async def route(self, origin, destination, mode):
            maps.append((origin.id, destination.id, mode))
            return RouteLeg(
                from_id=origin.id,
                to_id=destination.id,
                mode=mode,
                status="ok",
                duration_seconds=600,
                cost=10,
                cost_basis="total",
                cost_source="reference",
            )

    monkeypatch.setattr(routes_studio, "create_llm_provider", lambda settings: Model())
    monkeypatch.setattr(routes_studio, "create_search_provider", lambda settings: environment[3])
    monkeypatch.setattr(studio_tools, "AmapProvider", lambda *args: Maps())
    settings = Settings(_env_file=None, amap_api_key="isolated-test-map-key")
    environment[0]._transport.app.dependency_overrides[get_settings] = lambda: settings
    return result, calls, maps, Model(), settings


def workflow():
    graph = templates()[3].graph.model_copy(deep=True)
    graph.nodes[0].parameters.text = "上海出发，北京一天，两人，全团预算2000元"
    return graph


def artifact(data, node):
    return next(n for n in data["nodes"] if n["node_id"] == node)["artifact"]


async def test_structured_chain_checks_maps_calculates_and_persists_without_formal_write(
    environment, providers
):
    client, store, runtime, _ = environment
    _, bid, _ = await seed(environment)
    before = await latest(store, bid)
    checkpoint = await runtime.aget_state(thread_config(bid))
    await save(client, bid, workflow())
    response = await run(client, bid)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "succeeded", data
    assert len(providers[1]) == 1 and len(providers[2]) == 5
    plan = artifact(data, "plan")["plan"]
    route = artifact(data, "route")["plan"]
    budget = artifact(data, "budget")["budget"]
    assert plan["route_status"] == "unchecked" and not plan["days"][0]["legs"]
    assert all(a["place"] is None for a in plan["days"][0]["activities"])
    assert plan["days"][0]["activities"][0]["cost_source"] == "reference"
    assert plan["days"][0]["activities"][0]["source_indices"] == [1]
    assert route["route_status"] == "ready"
    assert [a["id"] for a in plan["days"][0]["activities"]] == [
        a["id"] for a in route["days"][0]["activities"]
    ]
    assert budget["overview"]["planned_cents"] == 14000
    assert budget["overview"]["unknown_count"] == 1
    assert budget["categories"]["ticket"] == 12000
    assert budget["categories"]["transport"] == 2000
    output = artifact(data, "document")
    assert output["plan"] == route and output["budget"] == budget
    assert "尚余" in output["text"] and "1860.00" in output["text"]
    assert output["sources"][0]["url"] == "https://example.com/info"
    assert (await store.workbench.run(bid, data["id"])).model_dump(mode="json") == data
    assert await latest(store, bid) == before
    assert await runtime.aget_state(thread_config(bid)) == checkpoint
    cached = (await run(client, bid, rid="structured-cache-0002")).json()
    assert all(n["status"] == "cached" for n in cached["nodes"])
    assert len(providers[1]) == 1 and len(providers[2]) == 5


async def test_route_parameters_and_provider_changes_invalidate_only_dependencies(
    environment, providers
):
    client, _, _, _ = environment
    _, bid, _ = await seed(environment)
    graph = workflow()
    await save(client, bid, graph)
    first = (await run(client, bid)).json()
    graph.nodes[3].parameters.travel_mode = "walking"
    await save(client, bid, graph, 1)
    changed = (await run(client, bid, revision=2, rid="structured-mode-0002")).json()
    states = {n["node_id"]: n["status"] for n in changed["nodes"]}
    assert states == {
        "brief": "cached",
        "research": "cached",
        "plan": "cached",
        "route": "succeeded",
        "budget": "succeeded",
        "document": "succeeded",
    }
    assert all(
        leg["mode"] == "walking" for leg in artifact(changed, "route")["plan"]["days"][0]["legs"]
    )
    assert len(providers[1]) == 1 and len(providers[2]) == 10
    original = provider_key(providers[4], "route")
    providers[4].amap_api_key = "changed-isolated-map-key"
    assert provider_key(providers[4], "route") != original
    again = (await run(client, bid, revision=2, rid="structured-config-0003")).json()
    assert next(n for n in again["nodes"] if n["node_id"] == "route")["status"] == "succeeded"
    assert artifact(first, "plan") == artifact(again, "plan")
    assert len(providers[1]) == 1 and len(providers[2]) == 15
    forced = (
        await run(
            client, bid, revision=2, rid="structured-budget-0004", target_node="budget", force=True
        )
    ).json()
    assert {n["node_id"]: n["status"] for n in forced["nodes"]}["route"] == "cached"
    assert len(providers[1]) == 1 and len(providers[2]) == 15


@pytest.mark.parametrize("problem", ["wrong_type", "two_plans", "missing_plan"])
async def test_structured_ports_reject_wrong_multiple_and_missing_data_before_calls(
    environment, providers, problem
):
    client, _, _, _ = environment
    _, bid, _ = await seed(environment)
    graph = workflow()
    if problem == "wrong_type":
        graph.edges[3].source_port = "text"
    elif problem == "two_plans":
        graph.edges.append(
            StudioEdge(
                source="plan", target="document", source_port="itinerary", target_port="itinerary"
            )
        )
    else:
        graph.edges = [e for e in graph.edges if e.target != "route"]
    response = await client.put(
        f"/api/branches/{bid}/studio",
        json={"expected_revision": 0, "graph": graph.model_dump(mode="json")},
    )
    if problem == "missing_plan":
        assert response.status_code == 200
        response = await run(client, bid)
    assert response.status_code == 400
    assert not providers[1] and not providers[2]


@pytest.mark.parametrize("problem", ["missing_day", "duplicate_day", "no_place", "non_plan"])
async def test_invalid_model_structure_blocks_downstream_without_writing_formal_trip(
    environment, providers, problem
):
    client, store, _, _ = environment
    _, bid, _ = await seed(environment)
    before = await latest(store, bid)
    if problem == "missing_day":
        providers[0].requirements.duration_days = 2
    elif problem == "duplicate_day":
        providers[0].days.append(providers[0].days[0].model_copy(deep=True))
    elif problem == "no_place":
        providers[0].days[0].activities[0].place_name = ""
    else:
        providers[0].adoptable = False
    await save(client, bid, workflow())
    data = (await run(client, bid)).json()
    states = {n["node_id"]: n["status"] for n in data["nodes"]}
    assert states["plan"] == "failed" and states["document"] == "blocked"
    assert not providers[2] and await latest(store, bid) == before


async def test_map_absence_failure_and_force_retry_keep_places_and_unknowns_honest(
    environment, providers, monkeypatch
):
    plan_artifact = await generate_plan(workflow().nodes[2], "需求", [], providers[3])
    source = plan_artifact.plan.model_copy(deep=True)
    no_map = await validate_plan(
        workflow().nodes[3], source, Settings(_env_file=None, amap_api_key=None)
    )
    assert no_map.route_status == "unconfigured"
    assert no_map.days[0].checked_at is None
    assert len(no_map.days[0].activities) == 3
    assert all(a.place is None for a in no_map.days[0].activities)

    class BrokenMaps:
        async def search_places(self, *args):
            raise ProviderError("隔离验收地图不可用")

    monkeypatch.setattr(studio_tools, "AmapProvider", lambda *args: BrokenMaps())
    failed = await validate_plan(workflow().nodes[3], source, providers[4])
    assert failed.route_status == "partial"
    assert all(a.place_status == "unavailable" for a in failed.days[0].activities)
    assert all(leg.duration_seconds is None for leg in failed.days[0].legs)
    assert calculate_budget(failed).overview.unknown_count == 3
    assert plan_artifact.plan == source


async def test_budget_unknown_people_basis_and_decimal_rounding(environment, providers):
    plan = (await generate_plan(workflow().nodes[2], "需求", [], providers[3])).plan
    plan.days[0].activities[0].cost = 0.105
    plan.requirements.travelers = 3
    plan.requirements.budget_basis = "per_person"
    view = calculate_budget(plan).overview
    assert view.planned_cents == 32 and view.budget_cents == 600000
    plan.requirements.travelers = None
    view = calculate_budget(plan).overview
    assert view.planned_cents == 0 and view.unknown_count == 2 and view.budget_cents is None
    assert not view.rows[0].settled and not view.rows[0].expense_ids


async def test_output_rejects_budget_from_another_plan_and_projection_does_not_smuggle_types(
    environment, providers
):
    plan_artifact = await generate_plan(workflow().nodes[2], "需求", [], providers[3])
    other = plan_artifact.plan.model_copy(deep=True)
    other.days[0].activities[0].cost += 1
    budget = calculate_budget(other)
    inputs = [
        {"title": "路线", "port": "itinerary", "artifact": plan_artifact.model_dump(mode="json")},
        {
            "title": "预算",
            "port": "budget",
            "artifact": NodeArtifact(text="预算", budget=budget).model_dump(mode="json"),
        },
    ]
    with pytest.raises(InvalidRequest, match="另一份行程"):
        await execute_node(workflow().nodes[-1], inputs, None, None, providers[4])
    assert project_artifact(plan_artifact, "sources").plan is None
    assert (
        project_artifact(NodeArtifact(text="hidden", plan=other, budget=budget), "budget").plan
        is None
    )
    assert (
        project_artifact(NodeArtifact(text="hidden", plan=other, budget=budget), "text").budget
        is None
    )


async def test_output_remaps_source_indices_and_mixed_prose_requires_extraction(
    environment, providers
):
    original = StudioSource(
        title="原资料", url="https://example.com/original", snippet="原票价", provider="fake"
    )
    added = StudioSource(
        title="额外资料", url="https://example.com/extra", snippet="补充", provider="fake"
    )
    plan_artifact = await generate_plan(workflow().nodes[2], "需求", [original], providers[3])
    inputs = [
        {
            "title": "补充资料",
            "port": "sources",
            "artifact": NodeArtifact(sources=[added]).model_dump(mode="json"),
        },
        {"title": "行程", "port": "itinerary", "artifact": plan_artifact.model_dump(mode="json")},
    ]
    output = await execute_node(workflow().nodes[-1], inputs, None, None, providers[4])
    assert output.plan.days[0].activities[0].source_indices == [2]
    assert "[2]" in output.text and output.sources == [added, original]
    assert plan_artifact.plan.days[0].activities[0].source_indices == [1]
    inputs.append(
        {
            "title": "自备文字",
            "port": "content",
            "artifact": NodeArtifact(text="不同安排").model_dump(mode="json"),
        }
    )
    mixed = await execute_node(workflow().nodes[-1], inputs, None, None, providers[4])
    assert mixed.plan is None and "不同安排" in mixed.text


async def test_structured_output_preview_and_first_adoption_need_no_second_model(
    environment, providers, monkeypatch
):
    client, store, runtime, _ = environment
    session = (await client.post("/api/sessions", json={})).json()
    bid = session["branches"][0]["id"]
    await save(client, bid, workflow())
    data = (await run(client, bid)).json()
    assert data["status"] == "succeeded"
    original = artifact(data, "document")["plan"]

    def unexpected_model(settings):
        raise AssertionError("结构化成稿不应再创建模型")

    monkeypatch.setattr(routes_studio, "create_llm_provider", unexpected_model)
    response = await preview(client, bid, rid=data["id"])
    assert response.status_code == 200, response.text
    candidate = response.json()
    assert not candidate["conflicts"]
    assert len(providers[1]) == 1
    assert not await store.list_versions(bid)
    result = await apply(client, bid, rid=data["id"])
    assert result.status_code == 200, result.text
    detail = result.json()
    assert len(await store.list_versions(bid)) == 1
    assert detail["requirements"] == original["requirements"]
    assert detail["itinerary"]["expenses"] == []
    assert detail["citations"][0]["url"] == "https://example.com/info"
    assert (await apply(client, bid, rid=data["id"])).json() == detail
