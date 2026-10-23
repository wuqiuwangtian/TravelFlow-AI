"""统一预算视图：保留计划，按已结算实付替换对应预估，不重复累加。"""

from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from app.domain.itinerary import BudgetItem, BudgetOverview, BudgetRow, ExpenseCategory, Itinerary

if TYPE_CHECKING:
    from app.domain.models import Requirements


def cents(value: float | None, basis: str, travelers: int | None) -> int | None:
    if value is None or basis == "unknown" or basis == "per_person" and travelers is None:
        return None
    return int(
        (
            Decimal(str(value))
            * (travelers if basis == "per_person" and travelers is not None else 1)
            * 100
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def budget_overview(itinerary: Itinerary, requirements: "Requirements") -> BudgetOverview:
    rows: dict[str, BudgetRow] = {}
    row: BudgetRow | None
    kinds: dict[str, ExpenseCategory] = {
        "meal": "meal",
        "hotel": "lodging",
        "attraction": "ticket",
        "station": "transport",
        "transport": "transport",
    }
    for day in itinerary.days:
        for activity in day.activities:
            if activity.is_context_anchor or activity.progress == "skipped":
                continue
            if activity.cost is None and activity.kind in {"station", "transport", "free_time"}:
                continue
            row = BudgetRow(
                id="stay-" + activity.id,
                title=activity.title,
                category=kinds.get(activity.kind, "other"),
                day=day.day,
                activity_id=activity.id,
                source="activity",
                planned_cents=cents(activity.cost, activity.cost_basis, requirements.travelers),
            )
            rows[row.id] = row
        for leg in day.legs:
            target = next((a for a in day.activities if a.id == leg.to_id), None)
            origin = next((a for a in day.activities if a.id == leg.from_id), None)
            if target is None or target.progress == "skipped":
                continue
            row = BudgetRow(
                id="leg-" + target.id,
                title=(
                    f"{origin.place_name if origin else '出发点'} → "
                    f"{target.place_name or target.title}"
                )[:100],
                category="transport",
                day=day.day,
                activity_id=target.id,
                source="transport",
                planned_cents=cents(leg.cost, leg.cost_basis, requirements.travelers),
            )
            rows[row.id] = row
    for item in itinerary.budget_items:
        if item.source == "custom" or item.id in rows:
            planned = rows.get(item.id)
            row = BudgetRow(**item.model_dump())
            if planned and item.source != "custom":
                row.day = planned.day
                row.activity_id = planned.activity_id
            rows[item.id] = row
    refunds: dict[str, int] = {}
    for a in itinerary.adjustments:
        if a.kind == "refund" and a.status == "received":
            refunds[a.expense_id] = refunds.get(a.expense_id, 0) + a.amount_cents
    for entry in itinerary.expenses:
        row = rows.get(entry.budget_item_id or "")
        if row is None and not entry.budget_item_id and entry.activity_id:
            linked = [
                r
                for r in rows.values()
                if r.activity_id == entry.activity_id and r.category == entry.category
            ]
            if len(linked) == 1:
                row = linked[0]
        if row is None and not entry.budget_item_id and entry.day is not None:
            candidates = [
                r
                for r in rows.values()
                if r.day == entry.day
                and r.category == entry.category
                and not r.id.startswith("expense-")
                and len(entry.title) >= 2
                and entry.title in r.title
            ]
            if len(candidates) == 1:
                row = candidates[0]
        if row is None:
            row = BudgetRow(
                id="expense-" + entry.id,
                title=entry.title,
                category=entry.category,
                day=entry.day,
                activity_id=entry.activity_id,
                source="custom",
                settled=True,
            )
            rows[row.id] = row
        row.expense_ids.append(entry.id)
        row.actual_cents += entry.amount_cents - refunds.get(entry.id, 0)
        row.settled = row.settled or entry.settles_budget
    result = BudgetOverview(rows=list(rows.values()))
    for row in result.rows:
        result.planned_cents += row.planned_cents or 0
        if row.settled:
            result.projected_cents += row.actual_cents
        else:
            result.projected_cents += max(row.planned_cents or 0, row.actual_cents)
            result.unknown_count += row.planned_cents is None
    result.budget_cents = cents(
        requirements.budget, requirements.budget_basis or "unknown", requirements.travelers
    )
    return result


def save_budget_item(itinerary: Itinerary, item: BudgetItem) -> None:
    for index, old in enumerate(itinerary.budget_items):
        if old.id == item.id:
            itinerary.budget_items[index] = item
            return
    itinerary.budget_items.append(item)


def apply_budget_overrides(itinerary: Itinerary, requirements: "Requirements") -> None:
    """用户改过的预估同步回时间轴与分日小计，模型修订不覆盖人工金额。"""
    from app.services.itinerary_service import summarise_day

    for day in itinerary.days:
        changed = False
        for item in itinerary.budget_items:
            activity = next((a for a in day.activities if a.id == item.activity_id), None)
            if activity is None or item.source == "custom":
                continue
            cost = item.planned_cents / 100 if item.planned_cents is not None else None
            if item.source == "activity":
                activity.cost, activity.cost_basis, activity.cost_source = cost, "total", "manual"
            else:
                activity.transport_cost, activity.transport_cost_basis = cost, "total"
                for leg in day.legs:
                    if leg.to_id == activity.id:
                        leg.cost, leg.cost_basis, leg.cost_source = cost, "total", "manual"
            changed = True
        if changed:
            summarise_day(day, requirements)
