import pytest
from linked_fixtures import linked_itinerary

from app.core.errors import InvalidRequest
from app.domain.itinerary import ExpenseCreate, ExpenseInput
from app.domain.models import Requirements
from app.services.adjustment_messages import handle_adjustment_message
from app.services.budget_service import budget_overview
from app.services.expense_dates import expense_matches_day, planned_expense_day
from app.services.expense_selection import expense_targets
from app.services.itinerary_service import sync_markdown
from app.services.travel_messages import handle_travel_message
from app.services.travel_service import add_expense, edit_expense


def plan():
    itinerary = linked_itinerary(with_receipt=True)
    itinerary.expenses[0].day = 1
    return itinerary


def test_payment_day_can_differ_from_activity_and_stays_when_editing_amount():
    itinerary = plan()
    old = itinerary.expenses[0].model_copy(deep=True)
    edit_expense(
        itinerary,
        old.id,
        ExpenseInput(
            title=old.title,
            amount="18",
            category=old.category,
            day=1,
            activity_id=old.activity_id,
            budget_item_id=old.budget_item_id,
            receipt_ids=old.receipt_ids,
            settles_budget=old.settles_budget,
            notes=old.notes,
        ),
    )
    current = itinerary.expenses[0]
    assert current.day == 1 and planned_expense_day(itinerary, current) == 2
    assert current.amount_cents == 1800 and current.receipt_ids == old.receipt_ids
    assert current.budget_item_id == old.budget_item_id and current.settles_budget
    add_expense(
        itinerary,
        ExpenseCreate(
            title="预付门票第二笔",
            amount="2",
            category="ticket",
            day=1,
            activity_id="park",
            budget_item_id="stay-park",
            request_id="prepaid-second",
        ),
    )
    row = next(r for r in budget_overview(itinerary, Requirements()).rows if r.id == "stay-park")
    assert row.day == 2 and row.actual_cents == 2000 and len(row.expense_ids) == 2


@pytest.mark.parametrize(
    "updates",
    [
        {"activity_id": "absent"},
        {"category": "meal"},
        {"day": 9},
    ],
)
def test_independent_payment_day_still_validates_category_activity_and_date(updates):
    itinerary = plan()
    data = dict(
        title="门票",
        amount="20",
        category="ticket",
        day=1,
        activity_id="park",
        budget_item_id="stay-park",
        request_id="invalid-payment-date",
    )
    with pytest.raises(InvalidRequest):
        add_expense(itinerary, ExpenseCreate(**(data | updates)))
    assert len(itinerary.expenses) == 1


def test_legacy_activity_link_uses_current_plan_without_double_counting():
    itinerary = plan()
    itinerary.expenses[0].budget_item_id = None
    itinerary.expenses[0].settles_budget = False
    itinerary.budget_items[0].day = 1  # 旧快照中的日期过期，按活动当前日期展示。
    rows = budget_overview(itinerary, Requirements()).rows
    row = next(r for r in rows if r.id == "stay-park")
    assert row.day == 2 and row.actual_cents == 2000
    assert not any(r.id.startswith("expense-") for r in rows)
    itinerary.expenses[0].activity_id = None
    itinerary.expenses[0].budget_item_id = "stay-park"
    assert planned_expense_day(itinerary, itinerary.expenses[0]) == 2


def test_chat_uses_payment_or_plan_day_and_never_loops_on_overlapping_dates():
    itinerary = plan()
    edited = handle_travel_message("第2天预付公园门票消费改成18元", itinerary, Requirements())
    assert edited.itinerary.expenses[0].amount_cents == 1800
    assert edited.itinerary.expenses[0].day == 1
    refunded = handle_adjustment_message("第2天预付公园门票退款到账5元", itinerary)
    assert refunded.itinerary.adjustments[0].expense_id == itinerary.expenses[0].id
    entry = itinerary.expenses[0]
    assert expense_matches_day(entry, 1, "付款日第1天", itinerary)
    assert not expense_matches_day(entry, 1, "行程日第1天", itinerary)
    assert not expense_matches_day(entry, 2, "付款日第2天", itinerary)
    add_expense(
        itinerary,
        ExpenseCreate(
            title=entry.title,
            amount="30",
            category="ticket",
            day=2,
            activity_id="park",
            budget_item_id="stay-park",
            request_id="other-payment-date",
        ),
    )
    ambiguous = handle_travel_message("第2天预付公园门票消费改成18元", itinerary, Requirements())
    assert ambiguous.clarification == "expense" and len(ambiguous.expense_candidates) == 2
    target = expense_targets(itinerary.expenses, itinerary, itinerary)[0]
    assert "付款第1天" in target.label and "行程第2天" in target.label


def test_export_labels_actual_payment_and_current_plan_day_separately():
    itinerary = plan()
    markdown = sync_markdown("测试", itinerary, Requirements())
    assert "付款日：第 1 天" in markdown and "关联行程：第 2 天" in markdown
    assert itinerary.expenses[0].day == 1
