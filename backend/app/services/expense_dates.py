"""付款日是账本事实，计划日从当前关联的活动／预算推导。"""

import re

from app.domain.itinerary import Expense, Itinerary


def planned_expense_day(itinerary: Itinerary, expense: Expense) -> int | None:
    activity_id = expense.activity_id
    if not activity_id and expense.budget_item_id:
        item = next((b for b in itinerary.budget_items if b.id == expense.budget_item_id), None)
        activity_id = item.activity_id if item else None
        if not activity_id and expense.budget_item_id.startswith(("stay-", "leg-")):
            activity_id = expense.budget_item_id.split("-", 1)[1]
        if not activity_id and item:
            return item.day
    return next((d.day for d in itinerary.days for a in d.activities if a.id == activity_id), None)


def expense_matches_day(expense: Expense, day: int | None, text: str, itinerary: Itinerary) -> bool:
    if day is None:
        return True
    if re.search(r"付款日|支付日|记账日", text):
        return expense.day == day
    if re.search(r"行程日|游玩日|计划日", text):
        return planned_expense_day(itinerary, expense) == day
    # 裸“第几天”可能指付款日或新的活动日；多笔命中时继续给真实候选，不猜一笔。
    return expense.day == day or planned_expense_day(itinerary, expense) == day
