"""回款独立记录，不覆盖原始消费；报销是资金来源，不重复冲减消费。"""

from datetime import datetime

from app.core.errors import ConflictError, InvalidRequest
from app.domain.itinerary import AdjustmentCreate, AdjustmentInput, ExpenseAdjustment, Itinerary


def adjustment_values(data: AdjustmentInput) -> dict:
    return {
        "expense_id": data.expense_id,
        "kind": data.kind,
        "amount_cents": int(data.amount * 100),
        "status": data.status,
        "notes": data.notes,
    }


def validate_adjustment(
    itinerary: Itinerary, data: AdjustmentInput, exclude_id: str | None = None
) -> None:
    expense = next((e for e in itinerary.expenses if e.id == data.expense_id), None)
    if expense is None:
        raise InvalidRequest("请先记录原始消费，再关联退款或报销")
    total = int(data.amount * 100) + sum(
        a.amount_cents
        for a in itinerary.adjustments
        if a.expense_id == data.expense_id and a.kind == data.kind and a.id != exclude_id
    )
    if total > expense.amount_cents:
        label = "退款" if data.kind == "refund" else "报销"
        raise InvalidRequest(f"这笔消费的{label}总额（含待到账）不能超过原始实付，请核对已有记录")


def add_adjustment(itinerary: Itinerary, data: AdjustmentCreate) -> bool:
    values = adjustment_values(data)
    existing = next((a for a in itinerary.adjustments if a.id == data.request_id), None)
    if existing:
        if all(getattr(existing, k) == v for k, v in values.items()):
            return False
        raise ConflictError("同一笔回款请求的内容发生变化，请刷新后重试")
    validate_adjustment(itinerary, data)
    if len(itinerary.adjustments) >= 4000:
        raise InvalidRequest("当前方案已达到4000笔回款记录上限")
    now = datetime.now()
    itinerary.adjustments.append(
        ExpenseAdjustment(id=data.request_id, **values, created_at=now, updated_at=now)
    )
    return True


def edit_adjustment(itinerary: Itinerary, adjustment_id: str, data: AdjustmentInput) -> None:
    entry = next((a for a in itinerary.adjustments if a.id == adjustment_id), None)
    if entry is None:
        raise InvalidRequest("回款记录不存在")
    validate_adjustment(itinerary, data, exclude_id=adjustment_id)
    for key, value in adjustment_values(data).items():
        setattr(entry, key, value)
    entry.updated_at = datetime.now()


def remove_adjustment(itinerary: Itinerary, adjustment_id: str) -> None:
    index = next((i for i, a in enumerate(itinerary.adjustments) if a.id == adjustment_id), None)
    if index is None:
        raise InvalidRequest("回款记录不存在")
    itinerary.adjustments.pop(index)
