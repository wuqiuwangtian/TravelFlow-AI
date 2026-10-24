import pytest
from conftest import FakeLLM, requirements
from test_activity_moves import seed
from test_studio import run, save
from test_studio_adoption import latest

from app.agents.checkpoint import thread_config
from app.api import routes_studio
from app.core.config import Settings, get_settings
from app.core.errors import InvalidRequest
from app.domain.itinerary import (
    Booking,
    DayDraft,
    DayPlan,
    Place,
    RouteLeg,
    ScheduledActivity,
)
from app.domain.studio_adoption import StudioPlanDraft
from app.domain.studio_data import StudioPlanData
from app.domain.workbench import StudioNode
from app.services import studio_tools
from app.services.studio_templates import templates
from app.services.studio_tools import calculate_budget, validate_plan
from app.services.studio_transform import optimize_day, select_places

NAMES = ["故宫博物院", "北海公园", "景山公园", "天坛公园"]
POSITIONS = [0, 3, 1, 2]


def plan(*, located=True):
    return StudioPlanData(
        requirements=requirements().model_copy(update={"duration_days": 1}),
        days=[
            DayPlan(
                day=1,
                title="候选景点",
                activities=[
                    ScheduledActivity(
                        id=f"site-{i}",
                        title=name,
                        place_name=name,
                        city="北京",
                        kind="attraction",
                        start_time=f"{9 + i * 2:02d}:00",
                        duration_minutes=60,
                        cost=10 * (i + 1),
                        cost_basis="total",
                        cost_source="estimate",
                        transport_minutes=20 + i,
                        transport_cost=5 + i,
                        transport_cost_basis="total",
                        source_indices=[1],
                        place=Place(
                            id=name,
                            name=name,
                            longitude=116.3 + POSITIONS[i] * 0.01,
                            latitude=39.9,
                            city="北京",
                            typecode="110000",
                        )
                        if located
                        else None,
                        place_status="matched" if located else "pending",
                    )
                    for i, name in enumerate(NAMES)
                ],
            )
        ],
    )


def selection(**parameters):
    return StudioNode(id="selection", kind="select", title="景点筛选", parameters=parameters)


def route(**parameters):
    return StudioNode(
        id="route",
        kind="route",
        title="路线校验",
        parameters={"route_order": "nearby", **parameters},
    )


@pytest.fixture
def providers(environment, monkeypatch):
    source = plan(located=False)
    calls, map_calls = [], []

    class Model(FakeLLM):
        async def structured(self, messages, schema, **kwargs):
            calls.append(messages)
            assert schema is StudioPlanDraft
            return StudioPlanDraft(
                adoptable=True,
                requirements=source.requirements,
                days=[DayDraft.model_validate(d.model_dump()) for d in source.days],
            )

    class Maps:
        async def search_places(self, keyword, city):
            map_calls.append((keyword, city))
            i = NAMES.index(keyword)
            return [
                Place(
                    id=keyword,
                    name=keyword,
                    longitude=116.3 + POSITIONS[i] * 0.01,
                    latitude=39.9,
                    city="北京",
                    typecode="110000",
                )
            ]

        async def route(self, origin, destination, mode):
            map_calls.append((origin.id, destination.id, mode))
            return RouteLeg(
                from_id=origin.id,
                to_id=destination.id,
                mode=mode,
                status="ok",
                duration_seconds=600,
                cost=3,
                cost_basis="total",
                cost_source="reference",
            )

    settings = Settings(_env_file=None, amap_api_key="isolated-geometry-test-key")
    environment[0]._transport.app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(routes_studio, "create_llm_provider", lambda _: Model())
    monkeypatch.setattr(routes_studio, "create_search_provider", lambda _: environment[3])
    monkeypatch.setattr(studio_tools, "AmapProvider", lambda *args: Maps())
    return source, calls, map_calls, settings


async def test_selection_keeps_required_removes_excluded_caps_count_and_rebuilds_budget():
    source = plan()
    before = source.model_copy(deep=True)
    selected = select_places(
        selection(
            max_attractions_per_day=2,
            required_places="天坛",
            excluded_places="北海",
            attraction_stay_minutes=120,
        ),
        source,
    )
    assert [a.place_name for a in selected.days[0].activities] == [NAMES[0], NAMES[3]]
    assert all(a.duration_minutes == 120 for a in selected.days[0].activities)
    assert selected.selection.before_count == 4 and selected.selection.after_count == 2
    removed = {d.title: d.reason for d in selected.selection.decisions if d.action == "removed"}
    assert "排除" in removed[NAMES[1]] and "上限" in removed[NAMES[2]]
    assert selected.days[0].activities[-1].transport_minutes is None
    assert selected.days[0].activities[-1].transport_cost is None
    assert calculate_budget(selected).overview.planned_cents == 5000
    assert source == before


async def test_protected_and_required_items_exceed_cap_without_losing_records():
    source = plan()
    first, second = source.days[0].activities[:2]
    first.progress = "completed"
    second.booking = Booking(start_time="11:00", duration_minutes=60)
    source.days[0].activities[2].cost_source = "manual"
    before = source.model_copy(deep=True)
    selected = select_places(
        selection(
            max_attractions_per_day=1,
            required_places="天坛",
            excluded_places="故宫",
            attraction_stay_minutes=180,
        ),
        source,
    )
    assert selected.days[0].activities[:3] == source.days[0].activities[:3]
    assert len(selected.days[0].activities) == 4
    assert "超过上限" in "".join(selected.warnings)
    assert selected.days[0].activities[-1].duration_minutes == 180
    assert source == before


@pytest.mark.parametrize("mode", ["manual", "rail", "flight", "transfer"])
async def test_selection_and_order_preserve_logistics_edge_endpoints(mode):
    source = plan()
    source.days[0].legs = [
        RouteLeg(
            from_id="site-0",
            to_id="site-1",
            mode="rail" if mode == "manual" else mode,
            cost=88,
            cost_basis="total",
            cost_source="manual" if mode == "manual" else "reference",
        )
    ]
    selected = select_places(
        selection(max_attractions_per_day=1, excluded_places="故宫、北海"), source
    )
    assert [a.id for a in selected.days[0].activities] == ["site-0", "site-1"]
    assert selected.days[0].legs[0] == source.days[0].legs[0]
    assert selected.days[0].activities[1].transport_mode == source.days[0].legs[0].mode
    assert selected.days[0].activities[1].transport_cost == 88
    optimized, _ = optimize_day(source.days[0])
    assert [a.id for a in optimized.activities[:2]] == ["site-0", "site-1"]


@pytest.mark.parametrize(
    "parameters,reason",
    [
        ({"required_places": "上游不存在的景点"}, "没有找到"),
        ({"required_places": "北海", "excluded_places": "北海公园"}, "同一景点"),
        ({"excluded_places": "故宫、北海、景山、天坛"}, "没有活动"),
        ({"required_places": ",".join(f"地点{i}" for i in range(21))}, "最多20"),
        ({"excluded_places": "。"}, "不能只有标点"),
    ],
)
async def test_invalid_or_empty_selection_fails_without_mutating_source(parameters, reason):
    source = plan()
    before = source.model_copy(deep=True)
    with pytest.raises(InvalidRequest, match=reason):
        select_places(selection(**parameters), source)
    assert source == before


async def test_optimizer_shortens_geometry_keeps_first_ids_and_all_activity_fields():
    source = plan()
    day = source.days[0]
    original = day.model_copy(deep=True)
    result, report = optimize_day(day)
    assert [a.id for a in result.activities] == ["site-0", "site-2", "site-3", "site-1"]
    assert report.changed and report.after_meters < report.before_meters
    assert "不代表实际" in report.reason
    assert {a.id: a for a in result.activities} == {a.id: a for a in original.activities}
    assert day == original


@pytest.mark.parametrize("problem", ["missing", "ambiguous", "cross_city"])
async def test_optimizer_keeps_order_when_coordinates_are_unconfirmed_or_cross_city(problem):
    day = plan().days[0]
    if problem == "missing":
        day.activities[2].place = None
    elif problem == "ambiguous":
        day.activities[2].place_status = "ambiguous"
    else:
        day.activities[2].place.city = "杭州"
    result, report = optimize_day(day)
    assert result == day and not report.changed
    assert "缺少确认坐标或跨城市" in report.reason


async def test_route_optimization_rechecks_new_edges_reschedules_and_preserves_manual_pair(
    environment, providers
):
    source = providers[0].model_copy(deep=True)
    before = source.model_copy(deep=True)
    result = await validate_plan(route(), source, providers[3])
    day = result.days[0]
    assert [a.id for a in day.activities] == ["site-0", "site-2", "site-3", "site-1"]
    assert [a.start_time for a in day.activities] == ["09:00", "10:10", "11:20", "12:30"]
    assert [(leg.from_id, leg.to_id) for leg in day.legs] == [
        ("site-0", "site-2"),
        ("site-2", "site-3"),
        ("site-3", "site-1"),
    ]
    assert result.optimization[0].changed and result.route_status == "ready"
    assert len(providers[2]) == 10  # 四次定位，原三段和新三段校验。
    assert source == before
    source.days[0].legs = [
        RouteLeg(
            from_id="site-0",
            to_id="site-1",
            mode="transit",
            cost=88,
            cost_basis="total",
            cost_source="manual",
        )
    ]
    source.days[0].activities[0].progress = "completed"
    original_first = source.days[0].activities[0].model_copy(deep=True)
    protected = await validate_plan(route(), source, providers[3])
    assert protected.days[0].activities[0].model_dump(
        exclude={"place", "place_status", "candidates", "match_reason"}
    ) == original_first.model_dump(exclude={"place", "place_status", "candidates", "match_reason"})
    assert (
        protected.days[0].legs[0].cost == 88 and protected.days[0].legs[0].cost_source == "manual"
    )


async def test_optimization_timeout_rolls_back_partial_order(environment, providers, monkeypatch):
    real_check = studio_tools.check_day
    calls = 0

    async def check(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TimeoutError
        return await real_check(*args, **kwargs)

    monkeypatch.setattr(studio_tools, "check_day", check)
    source = providers[0]
    result = await validate_plan(route(), source, providers[3])
    assert [a.id for a in result.days[0].activities] == [a.id for a in source.days[0].activities]
    assert not result.optimization[0].changed and "退回上游顺序" in result.optimization[0].reason
    assert result.route_status == "partial"


async def test_selection_change_reuses_model_and_invalidates_route_budget_and_output(
    environment, providers
):
    client, store, runtime, _ = environment
    _, bid, _ = await seed(environment)
    checkpoint = await runtime.aget_state(thread_config(bid))
    version = await latest(store, bid)
    graph = templates()[4].graph.model_copy(deep=True)
    graph.nodes[0].parameters.text = "北京一天，四个候选景点"
    graph.nodes[3].parameters.max_attractions_per_day = None
    await save(client, bid, graph)
    first = (await run(client, bid)).json()
    assert first["status"] == "succeeded", first
    assert len(providers[1]) == 1
    graph.nodes[3].parameters.max_attractions_per_day = 1
    graph.nodes[3].parameters.required_places = "天坛"
    await save(client, bid, graph, 1)
    changed = (await run(client, bid, revision=2, rid="select-changed-0002")).json()
    assert changed["status"] == "succeeded", changed
    assert {n["node_id"]: n["status"] for n in changed["nodes"]} == {
        "brief": "cached",
        "research": "cached",
        "plan": "cached",
        "selection": "succeeded",
        "route": "succeeded",
        "budget": "succeeded",
        "document": "succeeded",
    }
    assert len(providers[1]) == 1
    assert await latest(store, bid) == version
    assert await runtime.aget_state(thread_config(bid)) == checkpoint
    output = next(n for n in changed["nodes"] if n["node_id"] == "document")["artifact"]
    assert [a["place_name"] for a in output["plan"]["days"][0]["activities"]] == ["天坛公园"]
    assert output["plan"]["selection"]["after_count"] == 1
    graph.nodes[3].parameters.required_places = "不存在的景点"
    await save(client, bid, graph, 2)
    failed = (await run(client, bid, revision=3, rid="select-missing-0003")).json()
    assert next(n for n in failed["nodes"] if n["node_id"] == "selection")["status"] == "failed"
    assert next(n for n in failed["nodes"] if n["node_id"] == "document")["status"] == "blocked"
    assert len(providers[1]) == 1


async def test_invalid_keyword_count_is_rejected_before_model_or_maps(environment, providers):
    client, _, _, _ = environment
    _, bid, _ = await seed(environment)
    graph = templates()[4].graph.model_copy(deep=True)
    graph.nodes[0].parameters.text = "北京一天"
    graph.nodes[3].parameters.required_places = ",".join(f"地点{i}" for i in range(21))
    await save(client, bid, graph)
    assert (await run(client, bid)).status_code == 400
    assert not providers[1] and not providers[2]


async def test_slower_verified_map_order_is_rejected(environment, providers, monkeypatch):
    maps = studio_tools.AmapProvider(None, None)
    original_edges = set(zip(NAMES, NAMES[1:], strict=False))

    class SlowerMaps:
        search_places = maps.search_places

        async def route(self, origin, destination, mode):
            leg = await maps.route(origin, destination, mode)
            if (origin.name, destination.name) not in original_edges:
                leg.duration_seconds = 6000
            return leg

    monkeypatch.setattr(studio_tools, "AmapProvider", lambda *args: SlowerMaps())
    source = providers[0]
    result = await validate_plan(route(), source, providers[3])
    assert [a.id for a in result.days[0].activities] == [a.id for a in source.days[0].activities]
    report = result.optimization[0]
    assert not report.changed and "交通耗时增加" in report.reason
    assert report.before_travel_minutes == report.after_travel_minutes == 30
    assert all(leg.duration_seconds == 600 for leg in result.days[0].legs)
