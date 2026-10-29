"""选择精确消费；不通过把内部 ID 拼进自然语言来改变解析结果。"""

import hashlib
import re

from app.core.errors import InvalidRequest
from app.domain.clarification import ExpenseTarget
from app.domain.itinerary import Expense, Itinerary
from app.services.expense_dates import planned_expense_day

_STATION = r"第\s*(?:\d+|[一二三四五六七八九十两]+)\s*(?:站|个(?:停留)?点)"


def expense_matches_text(expense: Expense, text: str) -> bool:
    if expense.title in text:
        return True
    # 口语创建的标题可能保留「第4站午餐」。未再指定站序时允许按「午餐」查找，
    # 同名仍由候选选择处理；明确提到其他站序时不能跨站套用。
    title = re.sub(rf"^{_STATION}\s*", "", expense.title, count=1)
    return bool(
        title != expense.title
        and len(title) >= 2
        and title in text
        and not re.search(_STATION, text)
    )


def expense_fingerprint(expense: Expense) -> str:
    # 多项操作澄清后从原账本重放，新增消费的 UUID 和时间戳会重新生成。
    values = expense.model_dump_json(exclude={"id", "created_at", "updated_at"})
    return hashlib.sha256(values.encode()).hexdigest()


def expense_targets(
    candidates: list[Expense], working: Itinerary, original: Itinerary
) -> list[ExpenseTarget]:
    saved_ids = {e.id for e in original.expenses}
    indices = {e.id: i for i, e in enumerate(working.expenses)}
    targets = []
    for expense in candidates:
        labels = [
            f"付款第{expense.day}天" if expense.day is not None else "付款日未关联",
            expense.title,
            f"{expense.amount_cents / 100:.2f}元",
        ]
        planned_day = planned_expense_day(working, expense)
        if planned_day is not None and planned_day != expense.day:
            labels.append(f"行程第{planned_day}天")
        for day in working.days:
            for station, activity in enumerate(day.activities, 1):
                if activity.id == expense.activity_id:
                    labels.append(f"第{station}站 {activity.title}")
        if expense.notes:
            labels.append(expense.notes[:100])
        if expense.receipt_ids:
            labels.append(f"{len(expense.receipt_ids)}张凭证")
        if expense.id not in saved_ids:
            labels.append("本次新增")
        # 同日、同金额、同备注的消费也保持可区分的账本顺序。
        labels.append(f"账本第{indices[expense.id] + 1}笔")
        targets.append(
            ExpenseTarget(
                index=indices[expense.id],
                expense_id=expense.id if expense.id in saved_ids else None,
                fingerprint=expense_fingerprint(expense),
                label=" · ".join(labels),
            )
        )
    return targets


def selected_expense(
    target: ExpenseTarget, candidates: list[Expense], itinerary: Itinerary
) -> Expense:
    if target.index < len(itinerary.expenses):
        expense = itinerary.expenses[target.index]
        if (
            any(e.id == expense.id for e in candidates)
            and (target.expense_id is None or target.expense_id == expense.id)
            and target.fingerprint == expense_fingerprint(expense)
        ):
            return expense
    raise InvalidRequest("这笔消费已发生变化，请重新说明要操作的消费。")
