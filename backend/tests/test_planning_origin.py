import pytest

from app.agents.checkpoint import build_serde
from app.agents.itinerary_output import PlanDetails, prepare_itinerary
from app.core.config import Settings
from app.core.errors import InvalidRequest, LLMOutputInvalid, ProviderError
from app.domain.itinerary import (
    Activity,
    Booking,
    BudgetItem,
    DayDraft,
    DayPlan,
    ExpenseCreate,
    Itinerary,
    Place,
    ReplanInput,
    RouteLeg,
    ScheduledActivity,
)
from app.domain.models import Requirements
from app.services.budget_service import apply_budget_overrides, budget_overview
from app.services.itinerary_service import build_itinerary, check_day
from app.services.place_messages import handle_place_message
from app.services.recap_service import recap_view
from app.services.replan_service import set_replan_context
from app.services.travel_service import add_expense, set_progress


def sample():
    req = Requirements(origin="上海", destination="扬州", duration_days=2, travelers=2)
    previous = Itinerary(
        days=[
            DayPlan(
                day=1,
                activities=[
                    ScheduledActivity(
                        id="past",
                        title="已完成游览",
                        kind="attraction",
                        city="扬州",
                        progress="completed",
                        cost=125,
                        cost_basis="total",
                        cost_source="manual",
                    )
                ],
            ),
            DayPlan(
                day=2,
                activities=[
                    ScheduledActivity(
                        id="museum",
                        title="预约博物馆",
                        place_name="博物馆",
                        city="扬州",
                        kind="attraction",
                        booking=Booking(start_time="10:00", duration_minutes=120),
                        start_time="10:00",
                        duration_minutes=120,
                        transport_mode="driving",
                        transport_minutes=20,
                        transport_cost=30,
                        transport_cost_basis="total",
                        cost=20,
                        cost_basis="total",
                    )
                ],
            ),
        ]
    )
    previous.budget_items = [
        BudgetItem(
            id="stay-past",
            title="人工预算",
            category="ticket",
            source="activity",
            activity_id="past",
            day=1,
            planned_cents=12500,
        )
    ]
    add_expense(
        previous,
        ExpenseCreate(
            title="已付门票",
            amount="86.35",
            category="ticket",
            day=1,
            activity_id="past",
            request_id="protected-origin-expense",
        ),
    )
    apply_budget_overrides(previous, req)
    return previous, req


async def prepared():
    previous, req = sample()
    details = PlanDetails(
        days=[
            DayDraft(
                day=2,
                activities=[
                    Activity(
                        title="酒店出发点",
                        place_name="同名酒店",
                        city="扬州",
                        kind="hotel",
                        start_time="09:00",
                        is_planning_origin=True,
                    ),
                    Activity.model_validate(
                        {**previous.days[1].activities[0].model_dump(), "transport_mode": "transit"}
                    ),
                ],
            )
        ]
    )
    result, markdown = await prepare_itinerary(
        details,
        "原路书",
        req,
        Settings(_env_file=None, amap_api_key=None),
        0,
        previous=previous,
        scope_day=2,
    )
    return result, markdown, previous, req


async def test_future_origin_has_no_stay_fee_or_actual_visit_and_clears_stale_transport():
    result, markdown, previous, req = await prepared()
    origin, target = result.days[1].activities
    assert origin.is_context_anchor and origin.is_planning_origin
    assert origin.duration_minutes == origin.cost == 0
    assert origin.progress == "not_started" and origin.progress_updated_at is None
    assert result.days[1].replan_context is None
    assert target.id == "museum" and target.booking == previous.days[1].activities[0].booking
    assert target.transport_mode == "transit"
    assert target.transport_minutes is target.transport_cost is None
    assert result.days[1].legs[0].duration_seconds is result.days[1].legs[0].cost is None
    assert not any(row.activity_id == origin.id for row in budget_overview(result, req).rows)
    assert result.days[0] == previous.days[0]
    assert result.expenses == previous.expenses and result.budget_items == previous.budget_items
    assert "出发位置" in markdown
    assert all(
        "酒店" not in name for day in recap_view(result, req)["days"] for name in day["completed"]
    )
    assert build_serde().loads_typed(build_serde().dumps_typed(result)) == result


class Maps:
    def __init__(self, seconds=1200, failed=False):
        self.seconds, self.failed = seconds, failed
        self.modes = []

    async def search_places(self, keyword, city):
        return [Place(id=keyword, name=keyword, city=city, longitude=119.4, latitude=32.4)]

    async def route(self, origin, target, mode):
        self.modes.append(mode)
        if self.failed:
            raise ProviderError("地图路程暂不可用")
        return RouteLeg(
            from_id="",
            to_id="",
            mode=mode,
            status="ok",
            duration_seconds=self.seconds,
            cost=4,
            cost_basis="per_person",
            cost_source="reference",
        )


@pytest.mark.parametrize("seconds,failed", [(1200, False), (7200, False), (1200, True)])
async def test_route_rechecks_preserve_booking_and_keep_missing_values_unknown(seconds, failed):
    result, _, previous, req = await prepared()
    maps = Maps(seconds, failed)
    day = await check_day(result.days[1], req, maps)
    assert maps.modes == ["transit"]
    assert day.activities[0].duration_minutes == 0
    assert day.activities[1].booking == previous.days[1].activities[0].booking
    assert day.activities[1].start_time == "10:00"
    if failed:
        assert day.legs[0].duration_seconds is day.legs[0].cost is None
    else:
        assert day.legs[0].duration_seconds == seconds and day.legs[0].cost == 4
        assert any("冲突" in warning for warning in day.warnings) == (seconds == 7200)


@pytest.mark.parametrize(
    "activities",
    [
        [Activity(title="起点", is_planning_origin=True, cost=100)],
        [Activity(title="真实景点"), Activity(title="起点", is_planning_origin=True)],
    ],
)
def test_invalid_origin_role_cannot_hide_a_charge_or_a_later_stop(activities):
    with pytest.raises(LLMOutputInvalid):
        build_itinerary([DayDraft(day=1, activities=activities)], Requirements(duration_days=1))


def test_departure_and_later_lodging_at_same_place_keep_separate_roles_and_fee():
    req = Requirements(duration_days=1)
    result = build_itinerary(
        [
            DayDraft(
                day=1,
                activities=[
                    Activity(title="酒店", place_name="酒店", is_planning_origin=True),
                    Activity(title="酒店", place_name="酒店", kind="hotel", cost=300),
                ],
            )
        ],
        req,
    )
    origin, stay = result.days[0].activities
    assert origin.is_context_anchor and origin.cost == 0
    assert not stay.is_context_anchor and stay.cost == 300
    assert any(row.activity_id == stay.id for row in budget_overview(result, req).rows)


async def test_existing_booked_stop_cannot_be_reclassified_as_departure_only():
    previous, req = sample()
    details = PlanDetails(
        days=[
            DayDraft(
                day=2,
                activities=[
                    Activity(
                        title="预约博物馆",
                        place_name="博物馆",
                        city="扬州",
                        is_planning_origin=True,
                    )
                ],
            )
        ]
    )
    before = previous.model_dump()
    with pytest.raises(LLMOutputInvalid):
        await prepare_itinerary(
            details,
            "原路书",
            req,
            Settings(_env_file=None, amap_api_key=None),
            0,
            previous=previous,
            scope_day=2,
        )
    assert previous.model_dump() == before


async def test_planned_origin_becomes_current_position_only_after_explicit_departure():
    result, _, _, _ = await prepared()
    origin = result.days[1].activities[0]
    set_replan_context(result, 2, ReplanInput(activity_id=origin.id, time="09:30"))
    assert not origin.is_planning_origin and origin.is_context_anchor
    assert origin.progress == "in_progress" and origin.progress_updated_at is not None
    assert result.days[1].replan_context.time == "09:30"


@pytest.mark.parametrize("progress", ["in_progress", "completed", "skipped"])
async def test_planned_origin_cannot_be_marked_as_a_visit(progress):
    result, _, _, _ = await prepared()
    before = result.model_dump()
    with pytest.raises(InvalidRequest, match="规划起点不作为游览记录"):
        set_progress(result, 2, result.days[1].activities[0].id, progress)
    assert result.model_dump() == before


async def test_ambiguous_planning_origin_can_choose_place_without_lodging_or_arrival(monkeypatch):
    result, _, previous, req = await prepared()
    places = [
        Place(
            id=str(i),
            name=f"同名酒店({i}店)",
            city="扬州",
            longitude=119.4 + i / 100,
            latitude=32.4,
        )
        for i in (1, 2)
    ]

    async def search(*args, **kwargs):
        return places

    monkeypatch.setattr("app.services.place_messages._search", search)
    settings = Settings(_env_file=None, amap_api_key=None)
    pending = await handle_place_message("确认第2天第1站的地点", result, req, settings)
    assert pending.pending and pending.pending.candidates == places
    chosen = await handle_place_message("选第2个", result, req, settings, pending.pending)
    assert chosen.itinerary is not None
    origin = chosen.itinerary.days[1].activities[0]
    assert origin.place.id == "2"
    assert origin.is_planning_origin and origin.is_context_anchor and origin.cost == 0
    assert origin.progress == "not_started"
    assert not any(row.category == "lodging" for row in budget_overview(chosen.itinerary, req).rows)
    assert chosen.itinerary.expenses == previous.expenses
