"""常见旅中口语直接落记录；未知金额或歧义不猜测，也不调用模型。"""

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.core.errors import AppError
from app.domain.clarification import ClarificationKind, ExpenseTarget
from app.domain.itinerary import (
    ActivityProgress,
    Expense,
    ExpenseCategory,
    ExpenseCreate,
    ExpenseInput,
    Itinerary,
)
from app.domain.models import Requirements
from app.services.expense_selection import selected_expense
from app.services.travel_service import add_expense, edit_expense, remove_expense, set_progress

AMOUNT_PATTERN = r"(?<![0-9A-Za-z.,，+\-])([+-]?\d+(?:\.\d+)?)\s*(?:元|块钱?|RMB|CNY)"


@dataclass
class TravelReply:
    reply: str
    itinerary: Itinerary | None = None
    clarification: ClarificationKind | None = None
    expense_candidates: list[Expense] | None = None


def _number(text: str) -> int:
    if text.isdigit():
        return int(text)
    digits = dict(zip("零一二三四五六七八九两", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2], strict=True))
    if "十" in text:
        left, right = text.split("十", 1)
        return (digits.get(left, 1) if left else 1) * 10 + digits.get(right, 0)
    return digits.get(text, 0)


def handle_travel_message(
    text: str,
    itinerary: Itinerary,
    requirements: Requirements,
    *,
    expense_target: ExpenseTarget | None = None,
) -> TravelReply | None:
    text = re.sub(
        r"(?<!\d)(\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?!\d)",
        lambda match: match[1].replace(",", ""),
        text,
    )
    if re.search(
        r"如果|假如|预计|预算|估计|打算|计划|明天|能不能|能否|不要|不是|是否|吗|[?？]", text
    ):
        return None
    money_intent = bool(re.search(r"记账|记一笔|花了|付了|支付了|消费了|支出|记错了", text))
    if re.search(r"小时|分钟|公里", text) and not re.search(
        r"元|块|RMB|CNY|记账|记一笔", text, re.I
    ):
        money_intent = False
    editing = bool(re.search(r"更正|改成|改为|记错了|删除|移除|撤销", text))
    ledger_edit = editing and (money_intent or bool(re.search(r"账本|消费|实付|账单", text)))
    progress: ActivityProgress | None = (
        "not_started"
        if "恢复未开始" in text
        else "skipped"
        if re.search(r"跳过|不去这站", text)
        else "completed"
        if re.search(r"已完成|完成了|标记完成|逛完|游览完|游完", text)
        else "in_progress"
        if re.search(r"正在游览|正在逛|开始这一站|开始游览", text)
        else None
    )
    if not money_intent and not ledger_edit and not progress:
        return None
    if progress and re.search(r"没有|没逛|没游|未完成|还未|尚未|之后|以后|逛完后|游完后", text):
        return None
    if money_intent and progress:
        return TravelReply(
            "这句话同时包含活动状态和消费，请分开告诉我，例如「第一天故宫逛完了」「午餐花了86元」。"
        )
    result = itinerary.model_copy(deep=True)
    match_day = re.search(r"第\s*(\d+|[一二三四五六七八九十两]+)\s*天", text)
    day = _number(match_day[1]) if match_day else None
    if day is not None and not any(d.day == day for d in result.days):
        return TravelReply(
            "这个日期不在当前行程中，请说明第几天，或先调整旅行天数。", clarification="day"
        )
    if money_intent or ledger_edit:
        if re.search(r"退款|退了|报销", text):
            return TravelReply("退款或报销暂不作为新的消费记入；可以在消费明细中更正原记录。")
        amounts = re.findall(AMOUNT_PATTERN, text, flags=re.I)
        deleting = ledger_edit and bool(re.search(r"删除|移除|撤销", text))
        if not deleting and len(amounts) != 1:
            return TravelReply(
                "请给出一笔明确的实际金额，例如「午餐花了86元」；多笔消费可以分别告诉我。",
                clarification="amount" if not amounts else None,
            )
        if ledger_edit:
            candidates = [
                e for e in result.expenses if e.title in text and (day is None or e.day == day)
            ]
            if expense_target:
                entry = selected_expense(expense_target, candidates, result)
            elif len(candidates) != 1:
                if len(candidates) > 1 and (
                    len({e.day for e in candidates}) == 1 or any(e.day is None for e in candidates)
                ):
                    return TravelReply(
                        "有多笔符合描述的消费，请选择要修改或移除的那一笔。",
                        clarification="expense",
                        expense_candidates=candidates,
                    )
                return TravelReply(
                    "这笔消费还不能唯一确定，请说明消费名称和第几天，或在「消费明细」中更正。",
                    clarification="day"
                    if len(candidates) > 1 and len({e.day for e in candidates}) > 1
                    else None,
                )
            else:
                entry = candidates[0]
            if deleting:
                remove_expense(result, entry.id)
                return TravelReply(f"已移除「{entry.title}」这笔消费，历史版本仍保留。", result)
            try:
                updated = ExpenseInput(
                    title=entry.title,
                    amount=amounts[0],
                    category=entry.category,
                    day=entry.day,
                    activity_id=entry.activity_id,
                    notes=entry.notes,
                    receipt_ids=entry.receipt_ids,
                    budget_item_id=entry.budget_item_id,
                    settles_budget=entry.settles_budget,
                )
                edit_expense(result, entry.id, updated)
            except ValidationError:
                return TravelReply(
                    "实际金额需大于0，最多两位小数，请重新说明金额。", clarification="amount"
                )
            return TravelReply(
                f"已把「{entry.title}」更正为{updated.amount:.2f}元，原预算保持不变。", result
            )
        name = re.sub(r"第\s*(\d+|[一二三四五六七八九十两]+)\s*天", "", text)
        name = re.sub(AMOUNT_PATTERN, "", name, flags=re.I)
        name = re.sub(
            r"帮我|请|今天|刚才|刚刚|实际|一共|总共|记账|记一笔|花了|支付了|付了|消费了|支出|每人|人均",
            "",
            name,
        )
        name = name.strip(" ，,。:：；;！!")
        if not name or len(name) > 100:
            return TravelReply("这笔消费是什么内容？例如「第2天午餐花了86元」。")
        category: ExpenseCategory = (
            "meal"
            if re.search(r"餐|饭|小吃|早餐|咖啡|奶茶", name)
            else "lodging"
            if re.search(r"住宿|酒店|宾馆|民宿|房费", name)
            else "transport"
            if re.search(r"车|机票|地铁|接驳|交通|打的", name)
            else "ticket"
            if re.search(r"门票|景区票|入场", name)
            else "shopping"
            if re.search(r"购物|伴手礼|纪念品", name)
            else "other"
        )
        if day is None:
            active_days = {
                d.day for d in result.days for a in d.activities if a.progress == "in_progress"
            }
            if len(active_days) == 1:
                day = active_days.pop()
            elif requirements.departure_date:
                today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
                offset = (today - requirements.departure_date).days + 1
                day = offset if any(d.day == offset for d in result.days) else None
        try:
            data = ExpenseCreate(
                title=name, amount=amounts[0], category=category, day=day, request_id=uuid4().hex
            )
            if re.search(r"每人|人均", text):
                if requirements.travelers is None:
                    return TravelReply(
                        "这笔是人均金额，请再告诉我出行人数，或直接给出全团实际总额。"
                    )
                data.amount *= requirements.travelers
                data.notes = f"按每人{amounts[0]}元 × {requirements.travelers}人计入全团实付。"
                data = ExpenseCreate.model_validate(data.model_dump())
            add_expense(result, data)
        except ValidationError:
            return TravelReply(
                "实际金额需大于0，最多两位小数，请重新说明金额。", clarification="amount"
            )
        except AppError as exc:
            return TravelReply(exc.message)
        date_label = f"第{day}天" if day else "暂未关联日期"
        return TravelReply(
            f"已记下{date_label}「{name}」：全团实付{data.amount:.2f}元。原预算保持不变，可在消费明细中更正。",
            result,
        )
    days = [d for d in result.days if day is None or d.day == day]
    ordinal = re.search(r"第\s*(\d+|[一二三四五六七八九十两]+)\s*(?:站|个(?:停留)?点)", text)
    if ordinal:
        index = _number(ordinal[1]) - 1
        targets = [(d.day, d.activities[index]) for d in days if 0 <= index < len(d.activities)]
    else:
        targets = [
            (d.day, a)
            for d in days
            for a in d.activities
            if a.title in text
            or a.place_name
            and a.place_name in text
            or len(a.place_name.replace("博物院", "").replace("景区", "")) >= 2
            and a.place_name.replace("博物院", "").replace("景区", "") in text
        ]
    if len(targets) != 1:
        return TravelReply(
            "请说明哪一天、哪一站，例如「第1天第2站已完成」，也可以点击线路中的地点标记状态。",
            clarification="day"
            if len(targets) > 1 and len({number for number, _ in targets}) > 1
            else "activity",
        )
    number, activity = targets[0]
    assert progress is not None
    set_progress(result, number, activity.id, progress)
    label = {
        "not_started": "未开始",
        "in_progress": "进行中",
        "completed": "已完成",
        "skipped": "已跳过",
    }[progress]
    return TravelReply(f"第{number}天「{activity.title}」已记录为{label}。", result)
