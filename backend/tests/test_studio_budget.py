import pytest
from conftest import requirements
from pydantic import ValidationError
from test_studio import run, save
from test_studio_adoption import apply, latest, preview
from test_studio_tools import artifact, providers, workflow  # noqa: F401

from app.domain.itinerary import Activity, BudgetItem, DayDraft, Itinerary, RouteLeg
from app.domain.studio_data import BudgetRules, StudioPlanData
from app.domain.workbench import NodeParameters
from app.services import studio_adoption, studio_tools
from app.services.budget_service import budget_overview
from app.services.itinerary_service import build_itinerary
from app.services.studio_budget import supplements
from app.services.studio_tools import calculate_budget


@pytest.fixture(autouse=True)
def isolated_adoption_maps(monkeypatch):
    monkeypatch.setattr(
        studio_adoption, "AmapProvider", lambda *args: studio_tools.AmapProvider(*args)
    )


def plan():
    req = requirements().model_copy(update={"duration_days": 3, "travelers": 2})
    itinerary = build_itinerary(
        [
            DayDraft(
                day=i,
                activities=[
                    Activity(
                        title=f"景点{i}",
                        place_name=f"公园{i}",
                        kind="attraction",
                        cost=10,
                        cost_basis="total",
                    )
                ],
            )
            for i in range(1, 4)
        ],
        req,
    )
    return StudioPlanData(requirements=req, days=itinerary.days)


def rules():
    return BudgetRules(
        meal_per_person_day_cents=10000,
        lodging_per_room_night_cents=30000,
        lodging_rooms=1,
        round_trip_per_person_cents=50000,
        contingency_cents=20000,
    )


def test_allowances_cover_missing_categories_with_explicit_units_without_mutating_plan():
    source = plan()
    before = source.model_copy(deep=True)
    data = calculate_budget(source, rules())
    assert source == before
    assert data.overview.planned_cents == 243000
    assert data.overview.unknown_count == 0
    assert data.categories == {
        "meal": 60000,
        "lodging": 60000,
        "transport": 100000,
        "ticket": 3000,
        "shopping": 0,
        "other": 20000,
    }
    assert len(data.supplements) == 7
    assert [s.item.day for s in data.supplements if s.item.category == "lodging"] == [1, 2]
    assert all(s.item.source == "custom" and not s.item.activity_id for s in data.supplements)
    assert all(not r.settled and not r.expense_ids for r in data.overview.rows)
    assert all(s.explanation for s in data.supplements)


def test_default_rules_keep_old_behavior_and_missing_people_or_rooms_remain_unknown():
    source = plan()
    assert calculate_budget(source).overview.planned_cents == 3000
    assert not calculate_budget(source).supplements
    source.requirements.travelers = None
    config = rules().model_copy(update={"lodging_rooms": None})
    data = calculate_budget(source, config)
    assert data.overview.unknown_count == 6
    assert data.overview.planned_cents == 23000
    assert any("缺少人数" in w for w in data.warnings)
    assert any("缺少房间数" in w for w in data.warnings)
    config.meal_per_person_day_cents = 0
    assert calculate_budget(source, config).overview.unknown_count == 3


@pytest.mark.parametrize("amount,basis", [(None, "unknown"), (0, "total"), (50, "per_person")])
def test_existing_meal_including_free_or_unknown_blocks_daily_duplicate(amount, basis):
    source = plan()
    meal = (
        source.days[0]
        .activities[0]
        .model_copy(update={"id": "meal", "kind": "meal", "cost": amount, "cost_basis": basis})
    )
    source.days[0].activities.append(meal)
    data = calculate_budget(source, BudgetRules(meal_per_person_day_cents=10000))
    assert [s.item.day for s in data.supplements] == [2, 3]
    assert any("第1天饮食已有项目" in w for w in data.warnings)
    assert data.overview.unknown_count == (amount is None)


def test_custom_undated_budget_blocks_category_guess_and_existing_allowance_is_retained():
    source = plan()
    itinerary = Itinerary(
        days=source.days,
        budget_items=[
            BudgetItem(id="custom-meals", title="整趟饮食", category="meal", planned_cents=None),
            BudgetItem(
                id="studio-contingency",
                title="用户改过的备用金",
                category="other",
                planned_cents=1234,
            ),
        ],
    )
    items, notes = supplements(itinerary, source.requirements, rules())
    assert all(s.item.category != "meal" and s.item.id != "studio-contingency" for s in items)
    assert any("沿用现有金额" in w for w in notes)
    assert budget_overview(itinerary, source.requirements).unknown_count == 1
    assert itinerary.budget_items[1].planned_cents == 1234


@pytest.mark.parametrize("mode", ["rail", "flight", "transfer", "station", "transport", "custom"])
def test_existing_intercity_or_custom_transport_is_never_guessed_as_round_trip(mode):
    source = plan()
    itinerary = Itinerary(days=source.days)
    if mode in {"station", "transport"}:
        itinerary.days[0].activities[0].kind = mode
    elif mode == "custom":
        itinerary.budget_items = [
            BudgetItem(id="manual-transport", title="交通预留", category="transport")
        ]
    else:
        itinerary.days[0].legs = [RouteLeg(from_id="a", to_id="b", mode=mode)]
    items, notes = supplements(itinerary, source.requirements, rules())
    assert all(s.item.id != "studio-round-trip" for s in items)
    assert any("不另加整趟往返" in w for w in notes)


def test_one_day_needs_no_missing_night_and_local_transport_does_not_block_round_trip():
    source = plan()
    source.days = source.days[:1]
    source.requirements.duration_days = 1
    source.days[0].legs = [RouteLeg(from_id="a", to_id="b", mode="transit")]
    data = calculate_budget(source, rules())
    assert not any(s.item.category == "lodging" for s in data.supplements)
    assert any(s.item.id == "studio-round-trip" for s in data.supplements)


@pytest.mark.parametrize(
    "field,value",
    [
        ("lodging_rooms", 0),
        ("lodging_rooms", 1.5),
        ("meal_per_person_day_cents", -1),
        ("contingency_cents", 100_000_001),
        ("round_trip_per_person_cents", 1.1),
        ("unexpected", 10),
    ],
)
def test_invalid_rule_values_reject_before_execution(field, value):
    with pytest.raises(ValidationError):
        NodeParameters(budget_rules={field: value})


async def test_rules_change_only_budget_dependencies_and_adoption_uses_them_without_model(
    environment,
    providers,  # noqa: F811
):
    client, store, _, _ = environment
    session = (await client.post("/api/sessions", json={})).json()
    bid = session["branches"][0]["id"]
    graph = workflow()
    await save(client, bid, graph)
    first = (await run(client, bid)).json()
    graph.nodes[-2].parameters.budget_rules = rules()
    await save(client, bid, graph, 1)
    second = (await run(client, bid, revision=2, rid="budget-rules-0002")).json()
    states = {n["node_id"]: n["status"] for n in second["nodes"]}
    assert states["plan"] == states["route"] == "cached"
    assert states["budget"] == states["document"] == "succeeded"
    assert len(providers[1]) == 1
    old = artifact(first, "budget")["budget"]
    new = artifact(second, "budget")["budget"]
    assert new["overview"]["planned_cents"] == old["overview"]["planned_cents"] + 140000
    assert not await store.list_versions(bid)
    prepared = await preview(client, bid, rid=second["id"])
    assert prepared.status_code == 200, prepared.text
    assert not prepared.json()["conflicts"]
    assert prepared.json()["budget_after"]["planned_cents"] == new["overview"]["planned_cents"]
    adopted = await apply(client, bid, rid=second["id"])
    assert adopted.status_code == 200, adopted.text
    assert len(adopted.json()["itinerary"]["budget_items"]) == 3
    assert not adopted.json()["itinerary"]["expenses"]
    assert len(providers[1]) == 1
    assert (await apply(client, bid, rid=second["id"])).json() == adopted.json()
    source = await store.workbench.run(bid, second["id"])
    assert source.nodes[-1].artifact.budget.rules == rules()


async def test_adoption_rechecks_manual_budgets_and_keeps_previously_adopted_allowances(
    environment,
    providers,  # noqa: F811
):
    client, store, _, _ = environment
    session = (await client.post("/api/sessions", json={})).json()
    bid = session["branches"][0]["id"]
    graph = workflow()
    graph.nodes[-2].parameters.budget_rules = rules()
    await save(client, bid, graph)
    first = (await run(client, bid)).json()
    assert (await preview(client, bid, rid=first["id"])).status_code == 200
    adopted = (await apply(client, bid, rid=first["id"])).json()
    changed = await client.put(
        f"/api/versions/{adopted['version']['id']}/budget-items/studio-meal-day-1",
        json={"amount": "12.34"},
    )
    assert changed.status_code == 200, changed.text
    before = await latest(store, bid)
    graph.nodes[-2].parameters.budget_rules.meal_per_person_day_cents = 20000
    graph.nodes[-2].parameters.budget_rules.contingency_cents = 50000
    await save(client, bid, graph, 1)
    second = (await run(client, bid, revision=2, rid="budget-rules-0003")).json()
    prepared = await preview(client, bid, rid=second["id"], request_id="second-adoption-0001")
    assert prepared.status_code == 200, prepared.text
    data = prepared.json()
    assert not data["conflicts"]
    assert (
        data["itinerary"]["budget_items"]
        == before.itinerary.model_dump(mode="json")["budget_items"]
    )
    assert (
        next(r for r in data["budget_after"]["rows"] if r["id"] == "studio-meal-day-1")[
            "planned_cents"
        ]
        == 1234
    )
    assert any("已有预算" in w for w in data["warnings"])
    assert (await store.get_version_detail(before.version.id)) == before
