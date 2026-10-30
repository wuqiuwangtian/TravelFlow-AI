from datetime import datetime

import pytest
from test_itinerary_api import generate

from app.agents.checkpoint import build_serde, thread_config
from app.domain.itinerary import BudgetItem, DayPlan, ExpenseCreate, Itinerary, ScheduledActivity
from app.domain.models import Requirements, VersionDetail, VersionInfo
from app.export import render_html, render_markdown
from app.services.budget_service import budget_overview
from app.services.itinerary_service import summarise_day
from app.services.journey_messages import prepare_journey_message
from app.services.travel_service import add_expense


def test_export_reprojects_legacy_duplicate_cost_table_without_rewriting_the_snapshot():
    plan = Itinerary(
        days=[
            DayPlan(
                day=1,
                activities=[
                    ScheduledActivity(
                        id="lunch",
                        title="午餐（淮扬菜）",
                        kind="meal",
                        cost=125,
                        cost_basis="total",
                    )
                ],
            )
        ]
    )
    add_expense(
        plan,
        ExpenseCreate(
            title="第4站午餐",
            amount="86.35",
            category="meal",
            day=1,
            request_id="export-legacy-station",
        ),
    )
    req = Requirements(duration_days=1)
    summarise_day(plan.days[0], req)
    detail = VersionDetail(
        version=VersionInfo(id="v", branch_id="b", seq=1, created_at=datetime(2026, 10, 30)),
        markdown="## 五、费用预估\n旧错误小计211.35元",
        itinerary=plan,
        requirements=req,
    )
    before = detail.model_dump()
    for renderer in (render_markdown, render_html):
        output = renderer(detail)
        assert "211.35" not in output
        assert "125.00" in output and "86.35" in output
    assert detail.model_dump() == before


@pytest.mark.parametrize(
    "case",
    [
        "unique",
        "ambiguous",
        "other-day-row",
        "payment-other-day",
        "explicit-budget",
        "other-station",
    ],
)
def test_legacy_station_expense_matches_only_a_unique_same_day_category(case):
    plan = Itinerary(
        days=[
            DayPlan(
                day=1,
                activities=[
                    ScheduledActivity(
                        id="lunch",
                        title="第5站午餐" if case == "other-station" else "午餐（淮扬菜）",
                        kind="meal",
                        cost=125,
                        cost_basis="total",
                    )
                ],
            ),
            DayPlan(day=2),
        ]
    )
    if case == "ambiguous":
        plan.days[0].activities.append(
            ScheduledActivity(
                id="other", title="午餐（另一家）", kind="meal", cost=125, cost_basis="total"
            )
        )
    if case == "other-day-row":
        plan.days[1].activities.append(
            ScheduledActivity(
                id="other", title="午餐（另一家）", kind="meal", cost=125, cost_basis="total"
            )
        )
    if case == "explicit-budget":
        plan.budget_items.append(
            BudgetItem(
                id="custom-food",
                title="单独登记",
                category="meal",
                source="custom",
                planned_cents=2000,
            )
        )
    add_expense(
        plan,
        ExpenseCreate(
            title="第4站午餐",
            amount="86.35",
            category="meal",
            day=2 if case == "payment-other-day" else 1,
            budget_item_id="custom-food" if case == "explicit-budget" else None,
            request_id="legacy-station-meal",
        ),
    )
    original = plan.model_dump()
    overview = budget_overview(plan, Requirements())
    paid = next(row for row in overview.rows if row.expense_ids)
    expected = (
        "stay-lunch"
        if case in ["unique", "other-day-row"]
        else "custom-food"
        if case == "explicit-budget"
        else "expense-legacy-station-meal"
    )
    assert paid.id == expected
    assert paid.actual_cents == 8635
    if case == "unique":
        assert len(overview.rows) == 1 and overview.projected_cents == 12500
    assert plan.model_dump() == original


@pytest.mark.parametrize("ordinal,linked", [("第1站", True), ("第4站", False)])
def test_new_station_expense_keeps_title_and_links_only_an_existing_activity(ordinal, linked):
    plan = Itinerary(
        days=[
            DayPlan(
                day=1,
                activities=[
                    ScheduledActivity(
                        id="lunch", title="午餐", kind="meal", cost=125, cost_basis="total"
                    )
                ],
            )
        ]
    )
    result = prepare_journey_message(f"第1天{ordinal}午餐实付86.35元", plan, Requirements())
    assert result.itinerary is not None
    expense = result.itinerary.expenses[0]
    assert expense.title == ordinal + "午餐" and expense.amount_cents == 8635
    assert expense.activity_id == ("lunch" if linked else None)
    if linked:
        assert budget_overview(result.itinerary, Requirements()).projected_cents == 12500
    assert not plan.expenses


def test_projection_replaces_settled_estimates_and_does_not_double_count_partial_or_legacy():
    plan = Itinerary.model_validate(
        {
            "days": [
                {
                    "day": 1,
                    "activities": [
                        {
                            "id": "lunch",
                            "title": "午餐",
                            "kind": "meal",
                            "cost": 100,
                            "cost_basis": "total",
                        },
                        {
                            "id": "hotel",
                            "title": "住宿",
                            "kind": "hotel",
                            "cost": 200,
                            "cost_basis": "total",
                        },
                    ],
                }
            ]
        }
    )
    req = Requirements(budget=500, budget_basis="total")
    add_expense(
        plan,
        ExpenseCreate(
            title="午餐",
            amount="80",
            category="meal",
            day=1,
            activity_id="lunch",
            budget_item_id="stay-lunch",
            settles_budget=True,
            request_id="actual-lunch",
        ),
    )
    summary = budget_overview(plan, req)
    assert summary.planned_cents == 30000 and summary.projected_cents == 28000
    # 后续住宿尚未结算，押金50不能当作住宿全部费用，也不重复加到200上。
    add_expense(
        plan,
        ExpenseCreate(
            title="住宿押金",
            amount="50",
            category="lodging",
            day=1,
            activity_id="hotel",
            budget_item_id="stay-hotel",
            request_id="hotel-deposit",
        ),
    )
    assert budget_overview(plan, req).projected_cents == 28000
    # 旧账本同日同类且名称唯一匹配时进入对应行，不重复累加。
    legacy = Itinerary.model_validate({"days": plan.days})
    add_expense(
        legacy,
        ExpenseCreate(title="午餐", amount="80", category="meal", day=1, request_id="legacy-lunch"),
    )
    assert budget_overview(legacy, req).projected_cents == 30000
    assert len(budget_overview(legacy, req).rows) == 2


async def test_budget_edit_actual_custom_history_export_and_model_preservation(environment):
    client, store, graph, _ = environment
    sid, bid, before = await generate(client)
    vid = before["version"]["id"]
    row = next(r for r in before["budget_overview"]["rows"] if r["source"] == "activity")
    changed = await client.put(
        f"/api/versions/{vid}/budget-items/{row['id']}", json={"amount": "100"}
    )
    assert changed.status_code == 200, changed.text
    current = changed.json()
    assert current["itinerary"]["days"][0]["activities"][0]["cost"] == 100
    actual = {
        "title": row["title"],
        "amount": "80",
        "category": row["category"],
        "day": row["day"],
        "activity_id": row["activity_id"],
        "budget_item_id": row["id"],
        "settles_budget": True,
        "request_id": "budget-expense-1",
    }
    recorded = await client.post(f"/api/versions/{current['version']['id']}/expenses", json=actual)
    assert recorded.status_code == 200, recorded.text
    current = recorded.json()
    linked = next(r for r in current["budget_overview"]["rows"] if r["id"] == row["id"])
    assert linked["planned_cents"] == 10000 and linked["actual_cents"] == 8000 and linked["settled"]
    assert (await client.get(f"/api/versions/{vid}")).json() == before
    stale = await client.put(
        f"/api/versions/{vid}/budget-items/{row['id']}", json={"amount": "500"}
    )
    assert stale.status_code == 409
    custom = {
        "title": "伴手礼",
        "amount": "50",
        "category": "shopping",
        "day": 1,
        "request_id": "custom-shopping",
    }
    prior = current["version"]["id"]
    added = await client.post(f"/api/versions/{prior}/budget-items", json=custom)
    assert added.status_code == 200, added.text
    current = added.json()
    retry = await client.post(f"/api/versions/{prior}/budget-items", json=custom)
    assert retry.json()["version"]["id"] == current["version"]["id"]
    for fmt in ("md", "html"):
        response = await client.get(f"/api/versions/{current['version']['id']}/export?format={fmt}")
        assert "伴手礼" in response.text and "80.00" in response.text and "100.00" in response.text
    revision = await client.post(
        "/api/chat", json={"session_id": sid, "branch_id": bid, "message": "第二天轻松一点"}
    )
    assert revision.status_code == 200, revision.text
    snapshot = await graph.aget_state(thread_config(bid))
    assert snapshot.values["itinerary"].budget_items[0].planned_cents == 10000
    assert snapshot.values["itinerary"].days[0].activities[0].cost == 100
    restored = build_serde().loads_typed(build_serde().dumps_typed(snapshot.values))
    assert restored["itinerary"].budget_items[-1].title == "伴手礼"
    current_id = revision.json()["version_id"]
    removed = await client.delete(f"/api/versions/{current_id}/budget-items/custom-custom-shopping")
    assert removed.status_code == 200, removed.text
    assert all(r["title"] != "伴手礼" for r in removed.json()["budget_overview"]["rows"])


async def test_budget_refunds_preserve_gross_and_unlinked_or_invalid_items(environment):
    client, _, _, _ = environment
    _, _, before = await generate(client)
    vid = before["version"]["id"]
    row = before["budget_overview"]["rows"][0]
    payload = {
        "title": row["title"],
        "amount": "100",
        "category": row["category"],
        "day": row["day"],
        "budget_item_id": row["id"],
        "settles_budget": True,
        "request_id": "budget-refund-test",
    }
    for extra in [{"budget_item_id": "not-this-trip"}, {"day": 50}, {"category": "shopping"}]:
        wrong = await client.post(f"/api/versions/{vid}/expenses", json={**payload, **extra})
        assert wrong.status_code == 400
    saved = (await client.post(f"/api/versions/{vid}/expenses", json=payload)).json()
    eid = saved["itinerary"]["expenses"][0]["id"]
    returned = await client.post(
        f"/api/versions/{saved['version']['id']}/expense-adjustments",
        json={
            "expense_id": eid,
            "kind": "refund",
            "status": "received",
            "amount": "20",
            "request_id": "budget-refund",
        },
    )
    assert returned.status_code == 200, returned.text
    latest = returned.json()
    assert latest["itinerary"]["expenses"][0]["amount_cents"] == 10000
    assert (
        next(r for r in latest["budget_overview"]["rows"] if r["id"] == row["id"])["actual_cents"]
        == 8000
    )
    assert (
        await client.post(
            f"/api/versions/{latest['version']['id']}/budget-items",
            json={"title": "错误日期", "day": 50, "amount": "5", "request_id": "invalid-day"},
        )
    ).status_code == 400
