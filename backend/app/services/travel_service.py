"""旅中记录：金额按分存储，与预算独立；操作无需调用模型或地图。"""

from datetime import datetime

from app.core.errors import ConflictError, InvalidRequest
from app.domain.itinerary import (
    ActivityProgress,
    Expense,
    ExpenseCreate,
    ExpenseInput,
    Itinerary,
)


def expense_values(data: ExpenseInput) -> dict:
    return {
        "title": data.title.strip(),
        "amount_cents": int(data.amount * 100),
        "category": data.category,
        "day": data.day,
        "activity_id": data.activity_id,
        "notes": data.notes,
        "receipt_ids": list(dict.fromkeys(data.receipt_ids)),
        "budget_item_id": data.budget_item_id,
        "settles_budget": data.settles_budget,
    }


def validate_expense_link(itinerary: Itinerary, data: ExpenseInput) -> None:
    if data.settles_budget and not data.budget_item_id:
        raise InvalidRequest("请先关联预算项目，再将其标记为已结算")
    if data.budget_item_id:
        from app.domain.models import Requirements
        from app.services.budget_service import budget_overview

        row = next(
            (
                r
                for r in budget_overview(itinerary, Requirements()).rows
                if r.id == data.budget_item_id
            ),
            None,
        )
        if row is None or row.id.startswith("expense-"):
            raise InvalidRequest("预算项目不存在，请刷新后重新选择")
        if row.category != data.category:
            raise InvalidRequest("消费类别与预算项目不一致")
    if data.day is not None and not any(day.day == data.day for day in itinerary.days):
        raise InvalidRequest("记账日期不在当前行程中")
    if data.activity_id:
        linked = [
            a.id
            for day in itinerary.days
            for a in day.activities
            if a.id == data.activity_id and not a.is_context_anchor
        ]
        if not linked:
            raise InvalidRequest("关联活动不存在或不是实际停留点")


def add_expense(itinerary: Itinerary, data: ExpenseCreate) -> bool:
    values = expense_values(data)
    existing = next((e for e in itinerary.expenses if e.id == data.request_id), None)
    if existing:
        if all(getattr(existing, key) == value for key, value in values.items()):
            return False
        raise ConflictError("同一笔记账请求的内容发生变化，请刷新后重试")
    validate_expense_link(itinerary, data)
    if len(itinerary.expenses) >= 2000:
        raise InvalidRequest("当前方案已达到 2000 笔记录上限")
    now = datetime.now()
    itinerary.expenses.append(Expense(id=data.request_id, **values, created_at=now, updated_at=now))
    return True


def edit_expense(itinerary: Itinerary, expense_id: str, data: ExpenseInput) -> None:
    entry = next((e for e in itinerary.expenses if e.id == expense_id), None)
    if not entry:
        raise InvalidRequest("消费记录不存在")
    validate_expense_link(itinerary, data)
    for kind in ("refund", "reimbursement"):
        claimed = sum(
            a.amount_cents
            for a in itinerary.adjustments
            if a.expense_id == expense_id and a.kind == kind
        )
        if claimed > int(data.amount * 100):
            raise InvalidRequest("新金额小于已记录的退款或报销，请先更正相关回款记录")
    for key, value in expense_values(data).items():
        setattr(entry, key, value)
    entry.updated_at = datetime.now()


def remove_expense(itinerary: Itinerary, expense_id: str) -> None:
    if any(a.expense_id == expense_id for a in itinerary.adjustments):
        raise InvalidRequest("这笔消费已有退款或报销记录，请先移除相关回款记录")
    index = next((i for i, e in enumerate(itinerary.expenses) if e.id == expense_id), None)
    if index is None:
        raise InvalidRequest("消费记录不存在")
    itinerary.expenses.pop(index)


def set_progress(
    itinerary: Itinerary, day_number: int, activity_id: str, progress: ActivityProgress
) -> None:
    day = next((d for d in itinerary.days if d.day == day_number), None)
    activity = next((a for a in day.activities if a.id == activity_id), None) if day else None
    if not activity:
        raise InvalidRequest("活动不存在")
    activity.progress, activity.progress_updated_at = progress, datetime.now()


def preserve_travel_records(previous: Itinerary, current: Itinerary) -> None:
    """模型只能改未来安排；旅中历史由用户的明确操作修改。"""
    current.expenses = [entry.model_copy(deep=True) for entry in previous.expenses]
    current.adjustments = [entry.model_copy(deep=True) for entry in previous.adjustments]
    current.budget_items = [entry.model_copy(deep=True) for entry in previous.budget_items]
    current.recap = previous.recap.model_copy(deep=True) if previous.recap else None
    for old_day in previous.days:
        existing_day = next((d for d in current.days if d.day == old_day.day), None)
        if existing_day:
            existing_day.linked_replan = (
                old_day.linked_replan.model_copy(deep=True) if old_day.linked_replan else None
            )
            existing_day.replan_context = (
                old_day.replan_context.model_copy(deep=True) if old_day.replan_context else None
            )
        locked = [a for a in old_day.activities if a.progress != "not_started" or a.booking]
        if not locked:
            continue
        day = next((d for d in current.days if d.day == old_day.day), None)
        if day is None:
            current.days.append(old_day.model_copy(deep=True))
            continue
        restored: set[str] = set()
        for old in locked:
            index = next((i for i, a in enumerate(day.activities) if a.id == old.id), None)
            if index is None:
                index = next(
                    (
                        i
                        for i, a in enumerate(day.activities)
                        if a.id not in restored
                        and a.place_name == old.place_name
                        and a.city == old.city
                        and bool(old.place_name)
                    ),
                    None,
                )
            if index is not None:
                day.activities[index] = old.model_copy(deep=True)
            else:
                # 缺失的历史站点按原相对次序保留在后续安排之前。
                position = sum(a.progress != "not_started" for a in day.activities)
                day.activities.insert(position, old.model_copy(deep=True))
            restored.add(old.id)
    current.days.sort(key=lambda d: d.day)
    # 未来站点被替换后，保留实际消费，解除过期活动链接。
    ids = {a.id for d in current.days for a in d.activities}
    for entry in current.expenses:
        if entry.activity_id not in ids:
            entry.activity_id = None
        if (
            entry.budget_item_id
            and entry.budget_item_id.startswith(("stay-", "leg-"))
            and entry.budget_item_id.split("-", 1)[1] not in ids
        ):
            entry.budget_item_id = None
            entry.settles_budget = False
