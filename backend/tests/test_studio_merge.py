from datetime import UTC, date, datetime, timedelta

import pytest
from geo_fixtures import GeoMaps
from test_studio import run, save
from test_studio_adoption import apply, preview
from test_studio_candidates import CandidateModel, allocator, pool

from app.api import routes_studio
from app.core.config import Settings, get_settings
from app.core.errors import InvalidRequest
from app.domain.studio_candidates import CandidateLocation
from app.domain.workbench import NodeArtifact, StudioNode, StudioSource
from app.services import studio_location
from app.services.studio_candidates import allocate_candidates, render_candidates
from app.services.studio_engine import execution_order
from app.services.studio_merge import merge_candidates
from app.services.studio_templates import templates


def merger(**parameters):
    return StudioNode(
        id="merge", kind="merge", title="候选合并", parameters={"candidate_limit": 40, **parameters}
    )


def source(i):
    return StudioSource(
        title=f"资料{i}", url=f"https://example.com/source/{i}", snippet="隔离资料", provider="fake"
    )


def inputs(*pools):
    return [
        {
            "source": chr(97 + i),
            "title": f"分支{i + 1}",
            "port": "candidates",
            "artifact": NodeArtifact(candidates=p, sources=[source(i + 1)]).model_dump(mode="json"),
        }
        for i, p in enumerate(pools)
    ]


async def test_merge_deduplicates_and_preserves_each_source_suggestions_without_mutating_inputs():
    first = await pool()
    second = first.model_copy(deep=True)
    second.places[2].cost = 30
    second.places[2].duration_minutes = 120
    before = [first.model_dump_json(), second.model_dump_json()]
    result = merge_candidates(merger(), inputs(first, second), [source(1), source(2)])
    assert result.merge.before_count == 20 and result.merge.after_count == 10
    assert result.merge.duplicate_count == 10 and result.merge.omitted_count == 0
    north = result.places[2]
    assert north.cost is None and north.cost_basis == "unknown"
    assert north.duration_minutes == 60 and north.source_indices == [1]
    decision = result.merge.decisions[2]
    assert decision.conflicts == ["费用或口径不同", "建议停留不同"]
    assert [v.candidate.cost for v in decision.variants] == [10, 30]
    assert [v.candidate.source_indices for v in decision.variants] == [[1], [2]]
    assert [first.model_dump_json(), second.model_dump_json()] == before


async def test_explicit_priority_and_cost_policy_select_whole_priority_suggestion():
    first, second = await pool(), await pool()
    second.places[0].cost, second.places[0].reason = 30, "来自第二个分支的理由"
    result = merge_candidates(
        merger(merge_order=["b", "a"], merge_cost_policy="first"),
        inputs(first, second),
        [source(1), source(2)],
    )
    assert result.merge.source_order == ["b", "a"]
    assert result.places[0].cost == 30 and result.places[0].reason == second.places[0].reason
    assert result.places[0].source_indices == [2]
    assert any("保留报价不代表" in w for w in result.warnings)


async def test_missing_fields_are_not_synthesized_from_another_branch():
    first, second = await pool(), await pool()
    first.places[0].cost = first.places[0].duration_minutes = None
    result = merge_candidates(merger(), inputs(first, second), [source(1), source(2)])
    assert result.places[0].cost is None and result.places[0].duration_minutes is None
    assert result.merge.decisions[0].variants[1].candidate.cost == 10


@pytest.mark.parametrize(
    "field,value",
    [
        ("destination", "上海"),
        ("origin", "南京"),
        ("duration_days", 4),
        ("travelers", 3),
        ("budget", 999),
        ("budget_basis", "per_person"),
        ("departure_date", date(2027, 1, 1)),
    ],
)
async def test_conflicting_trip_facts_require_unified_inputs(field, value):
    first = await pool()
    first.requirements.departure_date = date(2026, 11, 1)
    second = first.model_copy(deep=True)
    setattr(second.requirements, field, value)
    with pytest.raises(InvalidRequest, match="不一致"):
        merge_candidates(merger(), inputs(first, second), [source(1), source(2)])


async def test_unknown_requirements_can_use_explicit_known_values_and_preferences_combine():
    first, second = await pool(), await pool()
    first.requirements.origin = None
    first.requirements.preferences = ["公园"]
    second.requirements.preferences = ["博物馆", "公园"]
    second.requirements.destination = "北京市"
    result = merge_candidates(merger(), inputs(first, second), [source(1), source(2)])
    assert result.requirements.origin == "上海"
    assert result.requirements.preferences == ["公园", "博物馆"]


async def test_limit_preserves_omitted_groups_and_all_variants():
    result = merge_candidates(
        merger(candidate_limit=3), inputs(await pool(), await pool()), [source(1), source(2)]
    )
    assert len(result.places) == 3 and result.merge.omitted_count == 7
    assert sum(d.action == "omitted" for d in result.merge.decisions) == 7
    assert sum(len(d.variants) for d in result.merge.decisions) == 20


async def test_citations_missing_from_union_are_removed_and_reference_cost_downgrades():
    result = merge_candidates(
        merger(merge_order=["b", "a"]), inputs(await pool(), await pool()), [source(1)]
    )
    assert result.places[0].source_indices == [] and result.places[0].cost_source == "estimate"
    assert any("来源上限" in w for w in result.warnings)
    assert result.merge.decisions[0].variants[1].candidate.source_indices == [1]


async def located(pool_data, poi, *, selected=None):
    found = (await GeoMaps().search_places(pool_data.places[2].place_name, "北京"))[0]
    pool_data.places[2].location = CandidateLocation(
        status="matched",
        place=found.model_copy(update={"id": poi}),
        selected_id=selected,
        reason="隔离定位",
        checked_at=datetime.now(UTC),
    )
    return pool_data


async def test_same_name_different_confirmed_places_or_choices_stay_separate_with_unique_ids():
    first = await located(await pool(), "one", selected="one")
    second = await located(await pool(), "two", selected="two")
    result = merge_candidates(merger(), inputs(first, second), [source(1), source(2)])
    north = [p for p in result.places if p.place_name == "北海公园"]
    assert len(north) == 2 and len({p.id for p in north}) == 2
    assert {p.location.selected_id for p in north} == {"one", "two"}
    assert sum(d.action == "separate" for d in result.merge.decisions) == 2
    assert result.merge.duplicate_count == 9


async def test_aliases_of_same_confirmed_poi_combine_but_different_cities_do_not():
    first, second = await located(await pool(), "same"), await located(await pool(), "same")
    second.places[2].place_name = "北海公园正门"
    result = merge_candidates(merger(), inputs(first, second), [source(1), source(2)])
    assert (
        len(result.places) == 10
        and result.merge.decisions[2].variants[1].candidate.place_name == "北海公园正门"
    )
    second.places[2].city = "上海"
    assert (
        len(merge_candidates(merger(), inputs(first, second), [source(1), source(2)]).places) == 11
    )


async def test_expired_pin_keeps_explicit_intent_but_cannot_supply_coordinates():
    first = await located(await pool(), "old", selected="old")
    first.places[2].location.checked_at = datetime.now(UTC) - timedelta(hours=25)
    result = merge_candidates(merger(), inputs(first), [source(1)])
    assert (
        result.places[2].location.place is None and result.places[2].location.selected_id == "old"
    )
    with pytest.raises(InvalidRequest, match="指定地图"):
        allocate_candidates(allocator(), result)


async def test_inherited_explicit_location_is_reverified_and_null_explicitly_clears_it(monkeypatch):
    maps = GeoMaps("ambiguous")
    monkeypatch.setattr(studio_location, "AmapProvider", lambda *args: maps)
    first = await pool()
    second = await located(await pool(), "north-west", selected="north-west")
    result = merge_candidates(merger(), inputs(first, second), [source(1), source(2)])
    cid = result.places[2].id
    assert result.places[2].location.selected_id == "north-west"
    node = StudioNode(id="location", kind="locate", title="定位")
    config = Settings(_env_file=None, amap_api_key="isolated-map-key")
    checked = await studio_location.locate_candidates(node, result, config)
    assert checked.places[2].location.place.id == "north-west"
    node.parameters.location_choices = {cid: None}
    cleared = await studio_location.locate_candidates(node, checked, config)
    assert (
        cleared.places[2].location.selected_id is None and cleared.places[2].location.place is None
    )


@pytest.mark.parametrize("problem", ["none", "too_many", "duplicate_priority", "wrong_type"])
def test_merge_inputs_validate_before_any_provider_call(problem):
    graph = templates()[7].graph.model_copy(deep=True)
    graph.nodes[0].parameters.text = "北京三天"
    if problem == "none":
        graph.edges = [e for e in graph.edges if e.target != "merge"]
    elif problem == "too_many":
        original = next(e for e in graph.edges if e.target == "merge")
        for i in range(7):
            graph.nodes.append(graph.nodes[2].model_copy(update={"id": f"extra{i}"}))
            graph.edges.append(original.model_copy(update={"source": f"extra{i}"}))
    elif problem == "duplicate_priority":
        next(n for n in graph.nodes if n.id == "merge").parameters.merge_order = [
            "culture",
            "culture",
        ]
    else:
        next(e for e in graph.edges if e.target == "merge").source_port = "text"
    with pytest.raises(InvalidRequest):
        execution_order(graph, "merge")


class MergeModel(CandidateModel):
    async def structured(self, messages, schema, **kwargs):
        result = await super().structured(messages, schema, **kwargs)
        if "户外与公园" in messages[0].content:
            result.places[2].cost = 30
        return result


@pytest.fixture
def providers(environment, monkeypatch):
    model = MergeModel()
    monkeypatch.setattr(routes_studio, "create_llm_provider", lambda settings: model)
    monkeypatch.setattr(routes_studio, "create_search_provider", lambda settings: environment[3])
    environment[0]._transport.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, amap_api_key=None
    )
    return model


async def test_real_chain_cache_export_and_adoption_keep_original_runs_and_require_no_extra_model(
    environment, providers
):
    client, store, _, _ = environment
    session = (await client.post("/api/sessions", json={})).json()
    bid = session["branches"][0]["id"]
    graph = templates()[7].graph.model_copy(deep=True)
    graph.nodes[0].parameters.text = "上海出发北京三天，两人，全团2000元"
    await save(client, bid, graph)
    first = (await run(client, bid)).json()
    assert first["status"] == "succeeded", first
    merged = next(n for n in first["nodes"] if n["node_id"] == "merge")
    assert merged["artifact"]["candidates"]["places"][2]["cost"] is None
    assert len(providers.calls) == 2 and not await store.list_versions(bid)
    next(n for n in graph.nodes if n.id == "merge").parameters.merge_cost_policy = "first"
    await save(client, bid, graph, 1)
    second = (await run(client, bid, revision=2, rid="merged-run-0002")).json()
    statuses = {n["node_id"]: n["status"] for n in second["nodes"]}
    assert (
        statuses["culture"] == statuses["outdoors"] == "cached" and statuses["merge"] == "succeeded"
    )
    assert len(providers.calls) == 2
    assert (await store.workbench.run(bid, first["id"])).model_dump(mode="json") == first
    prepared = await preview(client, bid, rid=second["id"])
    assert prepared.status_code == 200 and not prepared.json()["conflicts"], prepared.text
    adopted = await apply(client, bid, rid=second["id"])
    assert adopted.status_code == 200, adopted.text
    assert not adopted.json()["itinerary"]["expenses"] and len(providers.calls) == 2
    assert (await apply(client, bid, rid=second["id"])).json() == adopted.json()


async def test_maximum_pool_with_merge_report_stays_inside_text_limit():
    first, second = await pool(), await pool()
    for data in (first, second):
        data.places = [
            data.places[0].model_copy(
                deep=True,
                update={
                    "id": f"poi-{i:016x}",
                    "place_name": str(i) + "景" * 97,
                    "city": "城" * 50,
                    "reason": "理" * 600,
                },
            )
            for i in range(40)
        ]
    result = merge_candidates(merger(), inputs(first, second), [source(1), source(2)])
    assert len(result.places) == 40 and len(render_candidates(result)) <= 24000
