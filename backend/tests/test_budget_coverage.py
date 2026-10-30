import pytest

from app.domain.itinerary import BudgetItem, DayPlan, Itinerary, RouteLeg, ScheduledActivity
from app.domain.models import Requirements
from app.services.budget_service import budget_overview


def sample():
    plan = Itinerary(
        days=[
            DayPlan(
                day=day,
                activities=[
                    ScheduledActivity(
                        id=f"visit-{day}",
                        title="游览",
                        city="扬州",
                        cost=30,
                        cost_basis="total",
                    )
                ],
            )
            for day in (1, 2)
        ]
    )
    req = Requirements(
        origin="上海",
        destination="扬州",
        duration_days=2,
        budget=2000,
        budget_basis="total",
        travelers=2,
    )
    return plan, req


def test_omitted_categories_are_visible_without_inventing_rows_money_or_records():
    plan, req = sample()
    before = plan.model_dump()
    overview = budget_overview(plan, req)
    assert len(overview.coverage_warnings) == 3
    assert "住宿" in overview.coverage_warnings[0]
    assert "去程" in overview.coverage_warnings[1]
    assert "返程" in overview.coverage_warnings[2]
    assert overview.unknown_count == 0  # 已列项目均有价格，范围遗漏另外提示。
    assert overview.projected_cents == 6000 and len(overview.rows) == 2
    assert plan.model_dump() == before


@pytest.mark.parametrize("amount", [None, 0, 35000])
def test_explicit_whole_trip_reserves_cover_scope_even_when_unknown_or_zero(amount):
    plan, req = sample()
    plan.budget_items = [
        BudgetItem(
            id="lodging", title="住宿", category="lodging", source="custom", planned_cents=amount
        ),
        BudgetItem(
            id="transport",
            title="往返大交通预留",
            category="transport",
            source="custom",
            planned_cents=amount,
        ),
    ]
    overview = budget_overview(plan, req)
    assert overview.coverage_warnings == []
    assert overview.unknown_count == (2 if amount is None else 0)
    assert overview.projected_cents == 6000 + 2 * (amount or 0)


@pytest.mark.parametrize(
    "title,missing",
    [("去程车票", "返程"), ("返程车票", "去程"), ("打车", "去程"), ("不含往返交通", "去程")],
)
def test_local_or_one_way_budget_does_not_hide_other_trip_transport(title, missing):
    plan, req = sample()
    plan.budget_items.append(
        BudgetItem(
            id="transport", title=title, category="transport", source="custom", planned_cents=2000
        )
    )
    warnings = budget_overview(plan, req).coverage_warnings
    assert any(missing in warning for warning in warnings)


def test_real_outbound_and_return_legs_cover_transport_with_city_suffixes():
    plan, req = sample()
    plan.days[0].activities.insert(
        0, ScheduledActivity(id="start", title="上海站", city="上海市", kind="station")
    )
    plan.days[0].legs = [
        RouteLeg(from_id="start", to_id="visit-1", mode="rail", cost=120, cost_basis="per_person")
    ]
    plan.days[1].activities.append(
        ScheduledActivity(id="end", title="上海站", city="上海市", kind="station")
    )
    plan.days[1].legs = [
        RouteLeg(from_id="visit-2", to_id="end", mode="rail", cost=120, cost_basis="per_person")
    ]
    warnings = budget_overview(plan, req).coverage_warnings
    assert len(warnings) == 1 and "住宿" in warnings[0]
    plan.days[1].activities[-1].progress = "skipped"
    assert any("返程" in w for w in budget_overview(plan, req).coverage_warnings)


def test_each_missing_night_is_named_without_assuming_hotel_is_required():
    plan, req = sample()
    req.duration_days = 3
    plan.budget_items.append(
        BudgetItem(
            id="night1",
            title="住亲友家",
            day=1,
            category="lodging",
            source="custom",
            planned_cents=0,
        )
    )
    warning = budget_overview(plan, req).coverage_warnings[0]
    assert "第2晚" in warning and "第1晚" not in warning
    assert "是否需要支出" in warning


@pytest.mark.parametrize(
    "context,progress,expected",
    [(False, "not_started", True), (True, "not_started", False), (False, "skipped", False)],
)
def test_first_stop_transport_is_flagged_without_reassigning_or_charging_it(
    context, progress, expected
):
    plan, req = sample()
    first = plan.days[0].activities[0]
    first.is_context_anchor, first.progress = context, progress
    first.transport_mode, first.transport_cost, first.transport_minutes = "rail", 135, 127
    before = plan.model_dump()
    warnings = budget_overview(plan, req).coverage_warnings
    assert any("首站前" in warning for warning in warnings) == expected
    assert plan.model_dump() == before


@pytest.mark.parametrize("origin,destination", [("扬州市", "扬州"), (None, "扬州"), ("上海", None)])
def test_local_or_unknown_destination_does_not_invent_a_round_trip(origin, destination):
    plan, req = sample()
    req.origin, req.destination, req.duration_days = origin, destination, 1
    assert budget_overview(plan, req).coverage_warnings == []


@pytest.mark.parametrize("cost,minutes", [(0, 0), (0, 15), (None, 0)])
def test_zero_initial_transport_is_not_mistaken_for_an_unpriced_trip(cost, minutes):
    plan, req = sample()
    plan.days[0].activities[0].transport_cost = cost
    plan.days[0].activities[0].transport_minutes = minutes
    assert not any("首站前" in warning for warning in budget_overview(plan, req).coverage_warnings)


async def test_historical_exports_include_current_coverage_checks_without_rewriting_version(
    environment,
):
    from test_itinerary_api import generate

    client, store, _, _ = environment
    _, bid, before = await generate(client)
    vid = before["version"]["id"]
    detail = await store.get_version_detail(vid)
    original = detail.model_dump()
    warnings = detail.budget_overview.coverage_warnings
    assert any("去程" in warning for warning in warnings)
    for fmt in ("md", "html"):
        result = await client.get(f"/api/versions/{vid}/export?format={fmt}")
        assert result.status_code == 200
        assert "费用覆盖提醒" in result.text
        assert all(warning in result.text for warning in warnings)
    assert (await store.get_version_detail(vid)).model_dump() == original
    assert len(await store.list_versions(bid)) == 1
