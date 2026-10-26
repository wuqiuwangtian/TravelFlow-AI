import pytest
from candidate_fixtures import candidate_draft
from conftest import FakeLLM
from test_studio import run, save
from test_studio_adoption import apply, latest, preview

from app.api import routes_studio
from app.core.config import Settings, get_settings
from app.core.errors import InvalidRequest, LLMOutputInvalid
from app.domain.studio_candidates import CandidateDraft
from app.domain.workbench import NodeArtifact, StudioEdge, StudioNode, StudioSource
from app.services.studio_candidates import (
    allocate_candidates,
    generate_candidates,
    render_candidates,
)
from app.services.studio_engine import digest, execute_node, topology
from app.services.studio_templates import templates
from app.services.studio_tools import project_artifact


def workflow():
    graph = templates()[5].graph.model_copy(deep=True)
    graph.nodes[0].parameters.text = "上海出发，北京三天两人，全团预算2000元，城市漫步。"
    return graph


def allocator(**kwargs):
    return StudioNode(id="allocation", kind="allocate", title="分日分配", parameters=kwargs)


class CandidateModel(FakeLLM):
    def __init__(self):
        self.draft = candidate_draft()
        self.calls = []

    async def structured(self, messages, schema, **kwargs):
        assert schema is CandidateDraft
        self.calls.append(messages)
        return self.draft.model_copy(deep=True)


@pytest.fixture
def candidate_provider(environment, monkeypatch):
    model = CandidateModel()
    monkeypatch.setattr(routes_studio, "create_llm_provider", lambda settings: model)
    monkeypatch.setattr(routes_studio, "create_search_provider", lambda settings: environment[3])
    environment[0]._transport.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, amap_api_key=None
    )
    return model


async def pool(model=None, **kwargs):
    source = StudioSource(
        title="验收资料", url="https://example.com/candidates", snippet="景点资料", provider="fake"
    )
    node = StudioNode(
        id="candidates",
        kind="candidates",
        title="候选池",
        parameters={"prompt": "选具体景点", **kwargs},
    )
    return await generate_candidates(node, "北京三天", [source], model or CandidateModel())


async def test_pool_deduplicates_bounds_references_and_ignores_self_reported_links():
    model = CandidateModel()
    model.draft.places.append(model.draft.places[0].model_copy(update={"city": "北京市"}))
    model.draft.places[0].source_indices = [1, 99, -1, 1]
    model.draft.places[0].reason = "参观 https://example.com/untrusted"
    first = await pool(model)
    assert len(first.places) == 10
    assert first.places[0].source_indices == [1]
    assert first.places[0].cost_source == "reference"
    assert "https://" not in first.model_dump_json()
    assert any("省略1项" in w for w in first.warnings)
    assert not first.model_dump().get("days")
    again = await pool(model)
    assert first == again
    bounded = await pool(model, candidate_limit=3)
    assert bounded.places == first.places[:3]


@pytest.mark.parametrize("problem", ["not_ready", "no_destination", "empty", "punctuation"])
async def test_invalid_candidate_output_fails_without_inventing_places(problem):
    model = CandidateModel()
    if problem == "not_ready":
        model.draft.ready = False
    elif problem == "no_destination":
        model.draft.requirements.destination = None
    elif problem == "empty":
        model.draft.places = []
    else:
        model.draft.places[0].place_name = "!!!"
    with pytest.raises(LLMOutputInvalid):
        await pool(model)


async def test_no_reference_is_an_estimate_and_unknowns_stay_unknown():
    model = CandidateModel()
    model.draft.places[0].source_indices = [99]
    model.draft.places[1].cost = None
    data = await pool(model)
    assert data.places[0].cost_source == "estimate"
    assert data.places[1].cost_source == "unknown"


async def test_balanced_allocates_each_day_and_explains_overflow_without_mutating_pool():
    source = await pool()
    before = source.model_copy(deep=True)
    result = allocate_candidates(allocator(), source)
    assert source == before
    assert [len(d.activities) for d in result.days] == [3, 3, 3]
    assert result.allocation.pool_count == 10 and result.allocation.selected_count == 9
    assert len([d for d in result.allocation.decisions if d.day is None]) == 1
    assert all(d.activities[0].start_time == "09:00" for d in result.days)
    assert all(
        a.kind == "attraction" and not a.place and a.progress == "not_started"
        for d in result.days
        for a in d.activities
    )
    assert all(
        leg.duration_seconds is None and leg.cost is None for d in result.days for leg in d.legs
    )
    assert result == allocate_candidates(allocator(), source)
    assert len({a.id for d in result.days for a in d.activities}) == 9


@pytest.mark.parametrize(
    "pace,days,expected",
    [("relaxed", 3, 6), ("full", 3, 10), ("balanced", 4, 10), ("balanced", 1, 3)],
)
async def test_same_pool_supports_different_paces_and_days(pace, days, expected):
    source = await pool()
    result = allocate_candidates(allocator(allocation_days=days, allocation_pace=pace), source)
    assert len(result.days) == days
    assert result.requirements.duration_days == days
    assert result.allocation.selected_count == expected
    assert all(d.activities for d in result.days)
    assert source.requirements.duration_days == 3


async def test_must_go_priority_exclusion_default_stay_and_late_start():
    source = await pool()
    source.places[-1].duration_minutes = None
    node = allocator(
        required_places="陶然亭",
        excluded_places="北海",
        allocation_days=2,
        allocation_pace="relaxed",
        allocation_default_stay_minutes=120,
        allocation_start_time="10:00",
        allocation_buffer_minutes=20,
    )
    result = allocate_candidates(node, source)
    names = [a.place_name for d in result.days for a in d.activities]
    assert "陶然亭公园" in names and "北海公园" not in names
    assert result.days[0].activities[0].place_name == "陶然亭公园"
    assert result.days[0].activities[0].duration_minutes == 120
    assert all(d.activities[0].start_time == "10:00" for d in result.days)
    assert any("默认120分钟" in d.reason for d in result.allocation.decisions)
    assert any("排除" in d.reason for d in result.allocation.decisions)
    assert (
        allocate_candidates(allocator(max_attractions_per_day=1), source).allocation.selected_count
        == 3
    )


@pytest.mark.parametrize(
    "problem,match",
    [
        ("missing", "没有找到必去"),
        ("contradiction", "同一候选"),
        ("all_excluded", "没有可分配"),
        ("too_many_days", "不足以覆盖"),
        ("cities", "同城"),
        ("no_days", "天数未知"),
        ("must_not_fit", "必去景点"),
        ("late", "无法形成"),
    ],
)
async def test_impossible_allocations_explain_repair_instead_of_faking_complete_days(
    problem, match
):
    source = await pool()
    params = {}
    if problem == "missing":
        params["required_places"] = "不存在的景点"
    elif problem == "contradiction":
        params.update(required_places="北海", excluded_places="北海公园")
    elif problem == "all_excluded":
        params["excluded_places"] = "、".join(p.place_name for p in source.places)
    elif problem == "too_many_days":
        params["allocation_days"] = 11
    elif problem == "cities":
        source.places[-1].city = "上海"
    elif problem == "no_days":
        source.requirements.duration_days = None
    elif problem == "must_not_fit":
        params.update(required_places="天坛", allocation_pace="relaxed")
        source.places[0].duration_minutes = 480
    else:
        params["allocation_start_time"] = "23:30"
    with pytest.raises(InvalidRequest, match=match):
        allocate_candidates(allocator(**params), source)


async def test_pool_port_projection_does_not_smuggle_itinerary_or_budget():
    source = await pool()
    artifact = NodeArtifact(text="候选", candidates=source)
    assert project_artifact(artifact, "candidates").candidates == source
    assert project_artifact(artifact, "text").candidates is None
    assert project_artifact(artifact, "sources").candidates is None
    with pytest.raises(InvalidRequest):
        project_artifact(artifact, "itinerary")


@pytest.mark.parametrize("problem", ["wrong_type", "multiple_pools", "direct_output"])
def test_new_port_rejects_invalid_connections(problem):
    graph = workflow()
    if problem == "wrong_type":
        next(e for e in graph.edges if e.target == "allocation").source_port = "text"
    elif problem == "multiple_pools":
        graph.nodes.append(graph.nodes[2].model_copy(update={"id": "second-pool"}))
        graph.edges.append(
            StudioEdge(
                source="second-pool",
                target="allocation",
                source_port="candidates",
                target_port="candidates",
            )
        )
    else:
        graph.edges.append(
            StudioEdge(
                source="candidates",
                target="document",
                source_port="candidates",
                target_port="itinerary",
            )
        )
    with pytest.raises(InvalidRequest):
        topology(graph)


async def test_shared_pool_branches_cache_independently_and_never_write_formal_plan(
    environment, candidate_provider
):
    client, store, _, _ = environment
    sid = (await client.post("/api/sessions", json={})).json()
    bid = sid["branches"][0]["id"]
    graph = workflow()
    graph.nodes.extend(
        [
            allocator(allocation_pace="relaxed").model_copy(update={"id": "slow"}),
            StudioNode(id="slow-document", kind="output", title="松弛成稿"),
        ]
    )
    graph.edges.extend(
        [
            StudioEdge(
                source="candidates",
                target="slow",
                source_port="candidates",
                target_port="candidates",
            ),
            StudioEdge(
                source="slow",
                target="slow-document",
                source_port="itinerary",
                target_port="itinerary",
            ),
        ]
    )
    await save(client, bid, graph)
    first = (await run(client, bid)).json()
    assert first["status"] == "succeeded", first
    document = next(n for n in first["nodes"] if n["node_id"] == "document")
    assert any("餐饮、住宿" in w for w in document["artifact"]["plan"]["warnings"])
    assert len(candidate_provider.calls) == 1
    graph.nodes[-2].parameters.allocation_days = 4
    await save(client, bid, graph, 1)
    second = (await run(client, bid, revision=2, rid="candidate-branch-0002")).json()
    states = {n["node_id"]: n["status"] for n in second["nodes"]}
    assert states["candidates"] == states["document"] == "cached"
    assert states["slow"] == states["slow-document"] == "succeeded"
    assert len(candidate_provider.calls) == 1
    assert not await store.list_versions(bid)
    assert (await store.workbench.run(bid, first["id"])).model_dump(mode="json") == first


async def test_full_candidate_chain_adopts_without_model_reextraction_and_retains_expenses(
    environment, candidate_provider
):
    client, store, _, _ = environment
    sid = (await client.post("/api/sessions", json={})).json()
    bid = sid["branches"][0]["id"]
    graph = workflow()
    graph.nodes[-2].parameters.budget_rules.meal_per_person_day_cents = 10000
    await save(client, bid, graph)
    first = (await run(client, bid)).json()
    prepared = await preview(client, bid, rid=first["id"])
    assert prepared.status_code == 200, prepared.text
    assert not prepared.json()["conflicts"]
    result = await apply(client, bid, rid=first["id"])
    assert result.status_code == 200, result.text
    adopted = result.json()
    assert len(adopted["itinerary"]["budget_items"]) == 3
    park = adopted["itinerary"]["days"][1]["activities"][0]
    expense = await client.post(
        f"/api/versions/{adopted['version']['id']}/expenses",
        json={
            "request_id": "candidate-expense-0001",
            "title": "景点实付",
            "amount": "15",
            "category": "ticket",
            "day": 2,
            "activity_id": park["id"],
        },
    )
    assert expense.status_code == 200, expense.text
    before = await latest(store, bid)
    graph.nodes[3].parameters.allocation_pace = "relaxed"
    await save(client, bid, graph, 1)
    second = (await run(client, bid, revision=2, rid="candidate-adopt-0002")).json()
    prepared = await preview(client, bid, rid=second["id"], request_id="candidate-second-0002")
    assert prepared.status_code == 200 and not prepared.json()["conflicts"], prepared.text
    applied = await apply(client, bid, rid=second["id"], request_id="candidate-second-0002")
    assert applied.status_code == 200, applied.text
    assert (
        applied.json()["itinerary"]["expenses"]
        == before.itinerary.model_dump(mode="json")["expenses"]
    )
    assert (await store.get_version_detail(before.version.id)) == before
    assert len(candidate_provider.calls) == 1


async def test_failed_rules_can_be_fixed_reusing_upstream_pool(environment, candidate_provider):
    client, store, _, _ = environment
    sid = (await client.post("/api/sessions", json={})).json()
    bid = sid["branches"][0]["id"]
    graph = workflow()
    graph.nodes[3].parameters.required_places = "不存在的必去"
    await save(client, bid, graph)
    first = (await run(client, bid)).json()
    states = {n["node_id"]: n["status"] for n in first["nodes"]}
    assert states["allocation"] == "failed" and states["document"] == "blocked"
    graph.nodes[3].parameters.required_places = "天坛"
    await save(client, bid, graph, 1)
    second = (await run(client, bid, revision=2, rid="candidate-repair-0002")).json()
    assert second["status"] == "succeeded"
    assert next(n for n in second["nodes"] if n["node_id"] == "candidates")["status"] == "cached"
    assert len(candidate_provider.calls) == 1
    assert not await store.list_versions(bid)


async def test_free_generation_can_use_candidate_data_with_bound_source_markers():
    source = await pool()
    artifact = project_artifact(
        NodeArtifact(
            candidates=source,
            sources=[
                StudioSource(
                    title="资料",
                    url="https://example.com/candidates",
                    snippet="资料",
                    provider="fake",
                )
            ],
        ),
        "candidates",
    )

    class Model:
        async def chat(self, messages, **kwargs):
            assert "[资料:" in messages[-1].content
            assert "天坛公园" in messages[-1].content
            return "比较候选，尚未分日。"

    result = await execute_node(
        StudioNode(
            id="compare", kind="generate", title="比较候选", parameters={"prompt": "比较取舍"}
        ),
        [{"title": "候选", "port": "candidates", "artifact": artifact.model_dump(mode="json")}],
        Model(),
        None,
        Settings(_env_file=None),
    )
    assert result.plan is None and result.candidates is None
    assert result.text == "比较候选，尚未分日。"


async def test_identical_pool_prose_with_different_sources_keeps_each_binding():
    data = await pool()
    sources = [
        StudioSource(title="资料", url=f"https://example.com/{i}", snippet="资料", provider="fake")
        for i in range(2)
    ]
    inputs = [
        {
            "title": f"候选{i}",
            "port": "candidates",
            "artifact": project_artifact(
                NodeArtifact(candidates=data, sources=[source]), "candidates"
            ).model_dump(mode="json"),
        }
        for i, source in enumerate(sources)
    ]

    class Model:
        async def chat(self, messages, **kwargs):
            context = messages[-1].content.split("\n【检索资料】")[0]
            for source in sources:
                assert context.count(f"[资料:{digest(str(source.url))[:8]}]") == 10
            return "各候选来源独立。"

    await execute_node(
        StudioNode(id="compare", kind="generate", title="比较", parameters={"prompt": "比较候选"}),
        inputs,
        Model(),
        None,
        Settings(_env_file=None),
    )


async def test_maximum_pool_renders_within_node_text_limit():
    model = CandidateModel()
    first = model.draft.places[0]
    model.draft.places = [
        first.model_copy(
            update={
                "place_name": f"{i}" + "景" * 97,
                "city": "城" * 50,
                "reason": "理" * 600,
                "source_indices": list(range(1, 21)),
            }
        )
        for i in range(40)
    ]
    source = await pool(model, candidate_limit=40)
    assert len(source.places) == 40
    assert len(render_candidates(source)) <= 24000
