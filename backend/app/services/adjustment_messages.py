"""退款/报销口语记账；只记录明确状态，不执行任何支付操作。"""

import re
from uuid import uuid4

from pydantic import ValidationError

from app.core.errors import AppError
from app.domain.clarification import ExpenseTarget
from app.domain.itinerary import AdjustmentCreate, AdjustmentInput, Itinerary
from app.services.adjustment_service import add_adjustment, edit_adjustment, remove_adjustment
from app.services.expense_selection import selected_expense
from app.services.travel_messages import AMOUNT_PATTERN, TravelReply, _number


def handle_adjustment_message(
    text: str, itinerary: Itinerary, *, expense_target: ExpenseTarget | None = None
) -> TravelReply | None:
    if not re.search(r"退款|退了|退回|报销", text):
        return None
    if re.search(r"如果|预计|打算|能不能|能否|是否|吗|[?？]|不要|不是|不退", text):
        return None
    text = re.sub(
        r"(?<!\d)(\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?!\d)", lambda m: m[1].replace(",", ""), text
    )
    kinds = set(re.findall(r"退款|退了|退回|报销", text))
    if "报销" in kinds and len(kinds) > 1:
        return TravelReply("请分别说明退款和报销，避免把两种回款重复计入。")
    kind = "reimbursement" if "报销" in kinds else "refund"
    label = "报销" if kind == "reimbursement" else "退款"
    day_match = re.search(r"第\s*(\d+|[一二三四五六七八九十两]+)\s*天", text)
    day = _number(day_match[1]) if day_match else None
    expenses = [e for e in itinerary.expenses if e.title in text and (day is None or e.day == day)]
    if expense_target:
        expense = selected_expense(expense_target, expenses, itinerary)
    elif len(expenses) != 1:
        if len(expenses) > 1 and (
            len({e.day for e in expenses}) == 1 or any(e.day is None for e in expenses)
        ):
            return TravelReply(
                "有多笔符合描述的原始消费，请选择这次回款对应的那一笔。",
                clarification="expense",
                expense_candidates=expenses,
            )
        return TravelReply(
            "请说明这笔回款对应哪项原始消费，例如「第2天门票退款到账20元」；原始消费还没记时请先记入。",
            clarification="day"
            if len(expenses) > 1 and len({e.day for e in expenses}) > 1
            else None,
        )
    else:
        expense = expenses[0]
    existing = [a for a in itinerary.adjustments if a.expense_id == expense.id and a.kind == kind]
    deleting = bool(re.search(r"删除|移除|撤销", text))
    editing = bool(re.search(r"更正|记错|改成|改为", text))
    if deleting and not re.search(r"记录|误记", text):
        return TravelReply("这里可以移除账本中误记的回款记录；实际退款或报销申请请在原平台处理。")
    amounts = re.findall(AMOUNT_PATTERN, text, re.I)
    if len(amounts) > 1:
        return TravelReply("请一次说明一笔回款金额，多笔回款可以分别记录。")
    if re.search(r"人均|每人", text):
        return TravelReply("退款和报销按原消费关联的全团金额记录，请给出本次总额。")
    pending = bool(
        re.search(
            r"申请|待到账|未到账|还没到账|没收到|未收到|未退|没退|未报销|没报销|退款中|报销中|已提交",
            text,
        )
    )
    received = not pending and bool(re.search(r"到账|已退|退了|退回|收到|实收|拿到", text))
    result = itinerary.model_copy(deep=True)
    try:
        if deleting:
            if len(existing) != 1:
                return TravelReply("这项消费有多笔或没有对应回款，请在回款明细中选择要移除的记录。")
            remove_adjustment(result, existing[0].id)
            return TravelReply(
                f"已移除「{expense.title}」的{label}记录，历史版本仍保留；实际资金未操作。", result
            )
        if editing:
            if len(existing) != 1 or not amounts:
                return TravelReply("请给出更正金额；有多笔回款时请在回款明细中选择对应记录。")
            old = existing[0]
            data = AdjustmentInput(
                expense_id=expense.id,
                kind=old.kind,
                amount=amounts[0],
                status="pending" if pending else "received" if received else old.status,
                notes=old.notes,
            )
            edit_adjustment(result, old.id, data)
        else:
            if not received and not pending:
                return TravelReply(
                    f"这笔{label}是已到账还是待到账？例如「{expense.title}{label}已到账20元」。"
                )
            waiting = [a for a in existing if a.status == "pending"]
            if received and amounts:
                waiting = [
                    a
                    for a in waiting
                    if a.amount_cents
                    == int(
                        AdjustmentInput(
                            expense_id=expense.id, kind=kind, amount=amounts[0], status="received"
                        ).amount
                        * 100
                    )
                ]
            if received and len(waiting) > 1:
                return TravelReply("有多笔同额待到账记录，请在回款明细中选择已到账的那一笔。")
            if received and len(waiting) == 1:
                old = waiting[0]
                data = AdjustmentInput(
                    expense_id=expense.id,
                    kind=old.kind,
                    amount=str(old.amount_cents / 100),
                    status="received",
                    notes=old.notes,
                )
                edit_adjustment(result, old.id, data)
            else:
                if not amounts:
                    return TravelReply(
                        "请给出这笔回款的总金额，例如「门票退款到账20元」。", clarification="amount"
                    )
                data = AdjustmentCreate(
                    expense_id=expense.id,
                    kind=kind,
                    amount=amounts[0],
                    status="pending" if pending else "received",
                    request_id=uuid4().hex,
                )
                add_adjustment(result, data)
    except ValidationError:
        return TravelReply(
            "回款金额需大于0，最多两位小数，请重新说明金额。", clarification="amount"
        )
    except AppError as exc:
        return TravelReply(exc.message)
    status = "已到账" if data.status == "received" else "待到账"
    return TravelReply(
        f"已记录「{expense.title}」{label}{data.amount:.2f}元（{status}），原始消费保持不变。",
        result,
    )
