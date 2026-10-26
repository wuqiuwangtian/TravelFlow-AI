import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from geo_fixtures import GeoMaps
from pydantic import ValidationError
from test_studio import run, save
from test_studio_adoption import apply, latest, preview
from test_studio_candidates import CandidateModel, allocator, pool

from app.api import routes_studio
from app.core.config import Settings, get_settings
from app.core.errors import InvalidRequest
from app.domain.workbench import NodeParameters, StudioEdge, StudioNode
from app.services import studio_adoption, studio_location, studio_tools
from app.services.studio_candidates import allocate_candidates, render_candidates
from app.services.studio_engine import execution_order
from app.services.studio_location import fresh_locations, locate_candidates
from app.services.studio_templates import templates


def workflow():
    graph = templates()[6].graph.model_copy(deep=True)
    graph.nodes[0].parameters.text = "上海出发，北京三天两人，全团预算2000元"
    return graph


def locator(**kwargs):
    return StudioNode(id="location", kind="locate", title="候选定位", parameters=kwargs)


@pytest.fixture
def maps(monkeypatch):
    provider = GeoMaps()
    for module in (studio_location, studio_tools, studio_adoption):
        monkeypatch.setattr(module, "AmapProvider", lambda *args: provider)
    return provider


def settings():
    return Settings(_env_file=None, amap_api_key="isolated-geo-test-key")


@pytest.fixture
def geo_provider(environment, monkeypatch, maps):
    model = CandidateModel()
    monkeypatch.setattr(routes_studio, "create_llm_provider", lambda config: model)
    monkeypatch.setattr(routes_studio, "create_search_provider", lambda config: environment[3])
    environment[0]._transport.app.dependency_overrides[get_settings] = settings
    return model, maps


async def test_locate_keeps_original_and_only_adds_server_locations_without_cost_changes(maps):
    source = await pool()
    before = source.model_copy(deep=True)
    located = await locate_candidates(locator(), source, settings())
    assert source == before
    assert located.location.status == "ready"
    assert len(maps.calls["search"]) == 10
    for p, original in zip(located.places, source.places, strict=True):
        assert p.location.status == "matched" and p.location.place.city == "北京"
        assert p.model_dump(exclude={"location"}) == original.model_dump(exclude={"location"})


@pytest.mark.parametrize(
    "mode,status",
    [
        ("ambiguous", "ambiguous"),
        ("missing", "not_found"),
        ("unavailable", "unavailable"),
        ("unknown_city", "ambiguous"),
        ("cross_city", "ambiguous"),
    ],
)
async def test_map_item_failures_do_not_fail_other_candidates_or_invent_coordinates(
    maps, mode, status
):
    maps.mode = mode
    located = await locate_candidates(locator(), await pool(), settings())
    item = located.places[2]
    assert located.location.status == "partial"
    assert item.location.status == status and item.location.place is None
    assert all(p.location.status == "matched" for p in located.places if p.id != item.id)
    assert "private-map-test-marker" not in located.model_dump_json()
    result = allocate_candidates(allocator(allocation_strategy="nearby"), located)
    assert not result.allocation.geography.applied
    assert result.allocation.geography.before_spread_meters is None
    assert [len(d.activities) for d in result.days] == [3, 3, 3]


async def test_unconfigured_lookup_discards_old_locations_and_geography_falls_back(maps):
    source = await locate_candidates(locator(), await pool(), settings())
    count = len(maps.calls["search"])
    unconfigured = await locate_candidates(
        locator(), source, Settings(_env_file=None, amap_api_key=None)
    )
    assert len(maps.calls["search"]) == count
    assert unconfigured.location.status == "unconfigured"
    assert all(p.location.place is None for p in unconfigured.places)
    result = allocate_candidates(allocator(allocation_strategy="nearby"), unconfigured)
    assert not result.allocation.geography.applied
    assert result.allocation.locations_checked_at is None


async def test_user_choice_must_still_exist_and_match_city_and_can_be_cleared(maps):
    maps.mode = "ambiguous"
    source = await pool()
    cid = source.places[2].id
    node = locator(location_choices={cid: "north-west"})
    chosen = await locate_candidates(node, source, settings())
    assert chosen.places[2].location.place.id == "north-west"
    maps.mode = "stale"
    stale = await locate_candidates(node, chosen, settings())
    assert stale.places[2].location.place is None
    assert "已不在" in stale.places[2].location.reason
    with pytest.raises(InvalidRequest, match="指定地图地点"):
        allocate_candidates(allocator(), stale)
    cleared = await locate_candidates(locator(location_choices={cid: None}), stale, settings())
    assert cleared.places[2].location.place.id == "north-new"
    maps.mode = "cross_city"
    node.parameters.location_choices[cid] = "geo-2"
    assert (await locate_candidates(node, source, settings())).places[2].location.place is None


async def test_geography_improves_spread_without_changing_selected_set_or_caps(maps):
    source = await locate_candidates(locator(), await pool(), settings())
    before = source.model_copy(deep=True)
    baseline = allocate_candidates(allocator(), source)
    grouped = allocate_candidates(allocator(allocation_strategy="nearby"), source)
    report = grouped.allocation.geography
    assert report.applied and report.after_spread_meters < report.before_spread_meters / 5
    assert source == before

    def names(plan):
        return sorted(a.place_name for d in plan.days for a in d.activities)

    assert names(grouped) == names(baseline)
    assert all(len(d.activities) == 3 for d in grouped.days)
    assert all(len({a.place.district for a in d.activities}) == 1 for d in grouped.days)
    assert grouped == allocate_candidates(allocator(allocation_strategy="nearby"), source)
    actual = {a.place_name: d.day for d in grouped.days for a in d.activities}
    assert all(
        decision.day == actual[decision.place_name]
        for decision in grouped.allocation.decisions
        if decision.day is not None
    )
    assert all(leg.duration_seconds is None for d in grouped.days for leg in d.legs)


async def test_required_excluded_and_daily_minutes_remain_constraints(maps):
    source = await locate_candidates(locator(), await pool(), settings())
    for p in source.places[::2]:
        p.duration_minutes = 180
    node = allocator(
        allocation_strategy="nearby",
        allocation_pace="relaxed",
        required_places="北海",
        excluded_places="故宫",
    )
    result = allocate_candidates(node, source)
    names = [a.place_name for d in result.days for a in d.activities]
    assert "北海公园" in names and "故宫博物院" not in names
    assert all(
        sum(a.duration_minutes for a in d.activities) + 30 * (len(d.activities) - 1) <= 240
        for d in result.days
    )
    assert all(len(d.activities) <= 2 for d in result.days)


@pytest.mark.parametrize("hours", [25, -1])
async def test_stale_or_future_locations_are_not_used_for_geography_or_route_pins(maps, hours):
    source = await locate_candidates(locator(), await pool(), settings())
    source.location.checked_at = datetime.now(UTC) - timedelta(hours=hours)
    result = allocate_candidates(allocator(allocation_strategy="nearby"), source)
    assert not result.allocation.geography.applied
    assert all(a.place is None for d in result.days for a in d.activities)
    assert not fresh_locations(source.location.checked_at)


async def test_route_reuses_recent_pins_but_rechecks_each_transport_leg(maps):
    source = await locate_candidates(locator(), await pool(), settings())
    plan = allocate_candidates(allocator(allocation_strategy="nearby"), source)
    node = StudioNode(id="route", kind="route", title="路线")
    checked = await studio_tools.validate_plan(node, plan, settings())
    assert len(maps.calls["search"]) == 10 and len(maps.calls["route"]) == 6
    assert checked.route_status == "ready"
    assert all(leg.status == "ok" for d in checked.days for leg in d.legs)
    checked.allocation.locations_checked_at = datetime.now(UTC) - timedelta(hours=25)
    await studio_tools.validate_plan(node, checked, settings())
    assert len(maps.calls["search"]) == 19 and len(maps.calls["route"]) == 12


async def test_bounded_timeout_keeps_completed_items_and_cancellation_propagates(maps, monkeypatch):
    maps.mode = "timeout"
    source = await pool()
    original_timeout = asyncio.timeout
    monkeypatch.setattr(
        studio_location.asyncio, "timeout", lambda seconds: original_timeout(min(seconds, 0.02))
    )
    located = await locate_candidates(locator(), source, settings())
    assert located.location.status == "partial"
    assert located.places[2].location.place is None
    assert any(p.location.status == "matched" for p in located.places)
    task = asyncio.create_task(locate_candidates(locator(), source, settings()))
    await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("problem", ["wrong_type", "multiple", "missing_input"])
def test_location_port_checks_fail_before_external_calls(problem):
    graph = workflow()
    if problem == "wrong_type":
        next(e for e in graph.edges if e.target == "location").source_port = "text"
    elif problem == "multiple":
        graph.nodes.append(graph.nodes[2].model_copy(update={"id": "other"}))
        graph.edges.append(
            StudioEdge(
                source="other",
                target="location",
                source_port="candidates",
                target_port="candidates",
            )
        )
    else:
        graph.edges = [e for e in graph.edges if e.target != "location"]
    with pytest.raises(InvalidRequest):
        execution_order(graph, "location")


@pytest.mark.parametrize(
    "values",
    [
        {"allocation_strategy": "shortest"},
        {"location_choices": {"invalid-id": "poi"}},
        {"location_choices": {"poi-0000000000000000": ""}},
    ],
)
def test_invalid_location_parameters_rejected(values):
    with pytest.raises(ValidationError):
        NodeParameters(**values)


async def test_lookup_cache_is_shared_force_lookup_refreshes_and_original_snapshot_is_unchanged(
    environment, geo_provider
):
    client, store, _, _ = environment
    model, maps = geo_provider
    sid = (await client.post("/api/sessions", json={})).json()
    bid = sid["branches"][0]["id"]
    graph = workflow()
    graph.nodes.extend(
        [
            StudioNode(
                id="slow",
                kind="allocate",
                title="松弛分支",
                parameters={"allocation_pace": "relaxed", "allocation_strategy": "nearby"},
            ),
            StudioNode(id="slow-document", kind="output", title="松弛成稿"),
        ]
    )
    graph.edges.extend(
        [
            StudioEdge(
                source="location", target="slow", source_port="candidates", target_port="candidates"
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
    assert len(maps.calls["search"]) == 10
    graph.nodes[-2].parameters.allocation_days = 4
    await save(client, bid, graph, 1)
    second = (await run(client, bid, revision=2, rid="geo-run-0002")).json()
    assert second["status"] == "succeeded"
    assert next(n for n in second["nodes"] if n["node_id"] == "location")["status"] == "cached"
    assert next(n for n in second["nodes"] if n["node_id"] == "document")["status"] == "cached"
    assert len(model.calls) == 1 and len(maps.calls["search"]) == 10
    forced = (
        await run(client, bid, revision=2, rid="geo-run-0003", target_node="location", force=True)
    ).json()
    assert forced["status"] == "succeeded" and len(maps.calls["search"]) == 20
    assert (await store.workbench.run(bid, first["id"])).model_dump(mode="json") == first
    assert not await store.list_versions(bid)


async def test_chosen_location_survives_preview_and_adoption_and_protected_actuals(
    environment, geo_provider
):
    client, store, _, _ = environment
    model, maps = geo_provider
    maps.mode = "ambiguous"
    sid = (await client.post("/api/sessions", json={})).json()
    bid = sid["branches"][0]["id"]
    source = await pool()
    graph = workflow()
    graph.nodes[3].parameters.location_choices = {source.places[2].id: "north-west"}
    await save(client, bid, graph)
    first = (await run(client, bid)).json()
    prepared = await preview(client, bid, rid=first["id"])
    assert prepared.status_code == 200 and not prepared.json()["conflicts"], prepared.text
    chosen = next(
        a
        for d in prepared.json()["itinerary"]["days"]
        for a in d["activities"]
        if a["place_name"] == "北海公园"
    )
    assert chosen["place"]["id"] == "north-west"
    adopted = await apply(client, bid, rid=first["id"])
    assert adopted.status_code == 200, adopted.text
    detail = adopted.json()
    assert len(maps.calls["search"]) == 10
    paid = await client.post(
        f"/api/versions/{detail['version']['id']}/expenses",
        json={
            "request_id": "geo-expense-0001",
            "title": "门票",
            "amount": "15",
            "category": "ticket",
            "activity_id": chosen["id"],
        },
    )
    assert paid.status_code == 200, paid.text
    before = await latest(store, bid)
    prepared = await preview(client, bid, rid=first["id"], request_id="geo-adoption-0002")
    assert prepared.status_code == 200 and not prepared.json()["conflicts"], prepared.text
    after = await apply(client, bid, rid=first["id"], request_id="geo-adoption-0002")
    assert after.status_code == 200, after.text
    assert (
        after.json()["itinerary"]["expenses"]
        == before.itinerary.model_dump(mode="json")["expenses"]
    )
    assert (await store.get_version_detail(before.version.id)) == before
    assert len(model.calls) == 1


async def test_maximum_located_pool_still_fits_rendered_text_limit(maps):
    source = await pool()
    source.places = [
        source.places[i % 10].model_copy(
            deep=True, update={"id": f"poi-{i:016x}", "reason": "理" * 600}
        )
        for i in range(40)
    ]
    located = await locate_candidates(locator(), source, settings())
    assert len(render_candidates(located)) <= 24000


async def test_failed_forced_location_evicts_old_success_and_plain_rerun_retries(
    environment, geo_provider
):
    client, _, _, _ = environment
    _, maps = geo_provider
    session = (await client.post("/api/sessions", json={})).json()
    bid = session["branches"][0]["id"]
    await save(client, bid, workflow())
    first = (await run(client, bid)).json()
    assert first["status"] == "succeeded"
    maps.mode = "unavailable"
    failed = (
        await run(client, bid, rid="failed-locate-0002", target_node="location", force=True)
    ).json()
    location = next(n for n in failed["nodes"] if n["node_id"] == "location")
    assert location["artifact"]["candidates"]["location"]["status"] == "partial"
    maps.mode = "complete"
    retried = (await run(client, bid, rid="retry-locate-0003")).json()
    location = next(n for n in retried["nodes"] if n["node_id"] == "location")
    assert location["status"] == "succeeded"
    assert location["artifact"]["candidates"]["location"]["status"] == "ready"
    assert len(maps.calls["search"]) == 30


async def test_expired_explicit_locations_require_refresh_in_allocation_and_route(maps):
    maps.mode = "ambiguous"
    source = await pool()
    located = await locate_candidates(
        locator(location_choices={source.places[2].id: "north-west"}), source, settings()
    )
    plan = allocate_candidates(allocator(allocation_strategy="nearby"), located)
    assert "north-west" in plan.allocation.explicit_locations.values()
    plan.allocation.locations_checked_at = datetime.now(UTC) - timedelta(hours=25)
    with pytest.raises(InvalidRequest, match="定位已过期"):
        await studio_tools.validate_plan(
            StudioNode(id="route", kind="route", title="路线"), plan, settings()
        )
    assert len(maps.calls["search"]) == 10 and not maps.calls["route"]
    located.location.checked_at = plan.allocation.locations_checked_at
    with pytest.raises(InvalidRequest, match="指定地图地点"):
        allocate_candidates(allocator(), located)


async def test_old_snapshot_with_expired_explicit_choice_cannot_silently_adopt_another_place(
    environment, geo_provider
):
    client, store, _, _ = environment
    _, maps = geo_provider
    maps.mode = "ambiguous"
    session = (await client.post("/api/sessions", json={})).json()
    bid = session["branches"][0]["id"]
    source = await pool()
    graph = workflow()
    graph.nodes[3].parameters.location_choices = {source.places[2].id: "north-west"}
    await save(client, bid, graph)
    run_data = (await run(client, bid)).json()
    saved = await store.workbench.run(bid, run_data["id"])
    document = next(n for n in saved.nodes if n.node_id == "document")
    document.artifact.plan.allocation.locations_checked_at = datetime.now(UTC) - timedelta(hours=25)
    await store.workbench.write_run(saved)
    response = await preview(client, bid, rid=saved.id)
    assert response.status_code == 400 and "定位已过期" in response.text
    assert not await store.list_versions(bid)
    assert len(maps.calls["search"]) == 10
