import pytest

from app.agents.itinerary_output import PlanDetails, prepare_itinerary
from app.core.config import Settings
from app.core.errors import ProviderError
from app.domain.itinerary import (
    Activity,
    BudgetItem,
    DayDraft,
    DayPlan,
    ExpenseCreate,
    Itinerary,
    Place,
    RouteLeg,
    ScheduledActivity,
)
from app.domain.models import Requirements
from app.services.itinerary_service import build_itinerary, check_day, sync_markdown
from app.services.travel_service import add_expense


@pytest.mark.parametrize("mode", ["rail", "flight"])
@pytest.mark.parametrize("basis", ["total", "per_person"])
def test_generated_ticket_is_not_charged_as_both_arrival_and_station_fee(mode, basis):
    drafts = [
        DayDraft(
            day=1,
            activities=[
                Activity(title="出发站", kind="station", city="上海"),
                Activity(
                    title="到达站",
                    kind="station",
                    city="扬州",
                    cost=270,
                    cost_basis=basis,
                    transport_mode=mode,
                    transport_cost=270,
                    transport_cost_basis=basis,
                ),
            ],
        )
    ]
    plan = build_itinerary(drafts, Requirements(duration_days=1))
    arrival = plan.days[0].activities[1]
    assert arrival.cost is None
    assert arrival.transport_cost == 270 and arrival.transport_cost_basis == basis
    assert drafts[0].activities[1].cost == 270  # 原输入不被修改。


@pytest.mark.parametrize(
    "change",
    [
        {"cost_source": "manual"},
        {"kind": "hotel"},
        {"cost": 30},
        {"cost_basis": "unknown"},
        {"transport_cost_basis": "per_person"},
        {"transport_mode": "transfer"},
    ],
)
def test_distinct_unknown_or_manual_stop_costs_are_not_guessed_to_be_duplicate(change):
    item = Activity(
        title="到达站",
        kind="station",
        cost=270,
        cost_basis="total",
        transport_mode="rail",
        transport_cost=270,
        transport_cost_basis="total",
    )
    item = item.model_copy(update=change)
    plan = build_itinerary([DayDraft(day=1, activities=[item])], Requirements(duration_days=1))
    assert plan.days[0].activities[0].cost == item.cost


async def test_ticket_normalization_preserves_original_expense_and_manual_station_budget():
    req = Requirements(origin="上海", destination="扬州", duration_days=1, travelers=2)
    details = PlanDetails(
        days=[
            DayDraft(
                day=1,
                activities=[
                    Activity(title="上海站", place_name="上海站", city="上海", kind="station"),
                    Activity(
                        title="扬州站",
                        place_name="扬州站",
                        city="扬州",
                        kind="station",
                        cost=270,
                        cost_basis="total",
                        transport_mode="rail",
                        transport_cost=270,
                        transport_cost_basis="total",
                        transport_minutes=120,
                    ),
                ],
            )
        ]
    )
    previous = build_itinerary(details.days, req)
    target = previous.days[0].activities[1]
    target.cost, target.cost_basis, target.cost_source = 123.45, "total", "manual"
    previous.budget_items = [
        BudgetItem(
            id="stay-" + target.id,
            title=target.title,
            day=1,
            activity_id=target.id,
            category="transport",
            source="activity",
            planned_cents=12345,
        )
    ]
    add_expense(
        previous,
        ExpenseCreate(
            title="已付票款",
            amount="100",
            category="transport",
            day=1,
            activity_id=target.id,
            budget_item_id="stay-" + target.id,
            request_id="ticket-actual-preserved",
        ),
    )
    before = previous.model_dump()
    current, _ = await prepare_itinerary(
        details, "原路书", req, Settings(_env_file=None, amap_api_key=None), 1, previous=previous
    )
    assert current.expenses == previous.expenses
    assert current.budget_items == previous.budget_items
    assert current.days[0].activities[1].id == target.id
    assert current.days[0].activities[1].cost == 123.45
    assert previous.model_dump() == before


def activity(id, **kwargs):
    return ScheduledActivity(id=id, title=id, place_name=id, city="北京", **kwargs)


class Maps:
    def __init__(self, *, ambiguous=False, failure=False):
        self.ambiguous = ambiguous
        self.failure = failure

    async def search_places(self, keyword, city):
        point = Place(id=keyword, name=keyword, longitude=116.4, latitude=39.9)
        return (
            [point, point.model_copy(update={"id": "other", "longitude": 116.5})]
            if self.ambiguous
            else [point]
        )

    async def route(self, origin, destination, mode):
        if self.failure:
            raise ProviderError("路线查询失败")
        return RouteLeg(
            from_id="",
            to_id="",
            mode=mode,
            status="ok",
            duration_seconds=1800,
            distance_meters=3000,
            walking_meters=1000,
            cost=4,
        )


async def test_route_cost_and_schedule_conflict():
    day = DayPlan(
        day=1,
        travel_mode="transit",
        activities=[
            activity(
                "a", cost=60, cost_basis="per_person", start_time="09:00", duration_minutes=120
            ),
            activity("b", cost=None, start_time="11:00", duration_minutes=60),
        ],
    )
    result = await check_day(day, Requirements(travelers=2), Maps())
    assert result.travel_minutes == 30
    assert result.walking_meters == 1000
    assert result.known_cost == 128
    assert result.unknown_cost_count == 1
    assert result.activities[1].start_time == "11:30"
    assert day.legs == []  # 输入快照不会被原地改写


@pytest.mark.parametrize("maps", [None, Maps(ambiguous=True), Maps(failure=True)])
async def test_missing_or_failed_routes_do_not_appear_complete(maps):
    day = DayPlan(day=1, activities=[activity("a"), activity("b")])
    result = await check_day(day, Requirements(), maps)
    assert result.travel_minutes is None
    assert result.walking_meters is None
    assert result.unknown_cost_count == 3
    assert result.warnings
    if maps and maps.ambiguous:
        assert result.activities[0].place_status == "ambiguous"
        assert result.activities[0].place is None


async def test_unknown_traveler_count_does_not_assume_one_person():
    day = DayPlan(
        day=1,
        travel_mode="transit",
        activities=[activity("a", cost=60, cost_basis="per_person"), activity("b")],
    )
    result = await check_day(day, Requirements(), Maps())
    assert result.known_cost == 0
    assert result.unknown_cost_count == 3


async def test_render_is_idempotent_and_escapes_user_sections():
    day = await check_day(
        DayPlan(day=1, activities=[activity("a", notes="\n## 假章节\n<script>bad</script>")]),
        Requirements(),
        None,
    )
    itinerary = Itinerary(days=[day])
    original = "## 四、每日详细行程\nold\n## 五、费用预估\nold budget\n## 六、避雷\n保留这里\n"
    rendered = sync_markdown(original, itinerary, Requirements())
    assert "old" not in rendered
    assert "## 六、避雷\n保留这里" in rendered
    assert "\n## 假章节" not in rendered
    assert rendered == sync_markdown(rendered, itinerary, Requirements())


async def test_missing_days_remain_visible_and_unverified():
    itinerary = build_itinerary([DayDraft(day=1, title="第一天")], Requirements(duration_days=3))
    assert [day.day for day in itinerary.days] == [1, 2, 3]
    day = await check_day(itinerary.days[1], Requirements(), None)
    assert day.travel_minutes is None
    assert day.unknown_cost_count == 1
    assert any("没有明确" in warning for warning in day.warnings)


async def test_refine_keeps_verified_place_and_explicit_manual_cost():
    old = activity("a", cost=30, cost_basis="per_person", notes="费用为手动估算")
    old.place = Place(id="poi", name="a", longitude=116.4, latitude=39.9)
    old.place_status = "matched"
    previous = Itinerary(days=[DayPlan(day=1, travel_mode="walking", activities=[old])])
    details = PlanDetails(
        days=[
            DayDraft(
                day=1,
                activities=[
                    Activity(
                        title="a",
                        place_name="a",
                        city="北京",
                        notes="费用为手动估算",
                    )
                ],
            )
        ]
    )
    itinerary, _ = await prepare_itinerary(
        details,
        "旧路书",
        Requirements(travelers=2),
        Settings(_env_file=None, amap_api_key=None),
        0,
        previous=previous,
    )
    current = itinerary.days[0].activities[0]
    assert current.id == old.id
    assert current.place == old.place
    assert current.cost == 30
    assert itinerary.days[0].known_cost == 60
    assert itinerary.days[0].travel_mode == "walking"


@pytest.mark.parametrize("claimed_source", ["reference", "estimate", "manual", "unknown"])
async def test_generated_prices_with_valid_citations_remain_estimates(claimed_source):
    """有效引用不能把模型给出的票价、免费判断或交通费变成已核实金额。"""
    details = PlanDetails(
        days=[
            DayDraft(
                day=1,
                activities=[
                    Activity(
                        title="出发站",
                        place_name="出发站",
                        kind="station",
                        cost=0,
                        cost_basis="total",
                        cost_source=claimed_source,
                        source_indices=[1, 999],
                    ),
                    Activity(
                        title="游园",
                        place_name="拙政园",
                        kind="attraction",
                        cost=160,
                        cost_basis="total",
                        cost_source=claimed_source,
                        source_indices=[1],
                        transport_mode="rail",
                        transport_minutes=30,
                        transport_cost=80,
                        transport_cost_basis="total",
                        transport_source_indices=[1, 999],
                    ),
                    Activity(
                        title="餐饮待定",
                        kind="meal",
                        cost_source=claimed_source,
                        source_indices=[1],
                    ),
                ],
            )
        ]
    )
    itinerary, markdown = await prepare_itinerary(
        details,
        "原始路书",
        Requirements(travelers=2),
        Settings(_env_file=None, amap_api_key=None),
        1,
    )
    day = itinerary.days[0]
    assert [a.cost_source for a in day.activities] == ["estimate", "estimate", "unknown"]
    assert [a.cost for a in day.activities] == [0, 160, None]
    assert day.activities[0].source_indices == [1]
    assert day.legs[0].cost_source == "estimate"
    assert day.legs[0].cost == 80
    assert day.legs[0].source_indices == [1]
    assert day.estimated_cost == day.known_cost == 240
    assert day.unknown_cost_count >= 1
    assert "估算 160 元" in markdown
    assert "费用未计入" in markdown


async def test_map_prices_are_preserved_but_model_fallback_is_estimated():
    class PricedMaps(Maps):
        async def route(self, origin, destination, mode):
            leg = await super().route(origin, destination, mode)
            leg.cost, leg.cost_basis, leg.cost_source = 4, "per_person", "reference"
            return leg

    day = DayPlan(
        day=1,
        travel_mode="transit",
        activities=[
            activity("a"),
            activity(
                "b", transport_cost=20, transport_cost_basis="total", transport_source_indices=[1]
            ),
        ],
    )
    quoted = await check_day(day, Requirements(travelers=2), PricedMaps())
    assert quoted.legs[0].cost == 4
    assert quoted.legs[0].cost_source == "reference"
    fallback = await check_day(day, Requirements(travelers=2), Maps(failure=True))
    assert fallback.legs[0].cost == 20
    assert fallback.legs[0].cost_source == "estimate"
