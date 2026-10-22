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
    }


def validate_expense_link(itinerary: Itinerary, data: ExpenseInput) -> None:
    if data.day is not None and not any(day.day == data.day for day in itinerary.days):
        raise InvalidRequest("记账日期不在当前行程中")
    if data.activity_id:
        linked = [
            day.day
            for day in itinerary.days
            if any(a.id == data.activity_id for a in day.activities)
        ]
        if not linked or data.day != linked[0]:
            raise InvalidRequest("关联活动与记账日期不一致")


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
    for key, value in expense_values(data).items():
        setattr(entry, key, value)
    entry.updated_at = datetime.now()


def remove_expense(itinerary: Itinerary, expense_id: str) -> None:
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
    for old_day in previous.days:
        existing_day = next((d for d in current.days if d.day == old_day.day), None)
        if existing_day:
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
