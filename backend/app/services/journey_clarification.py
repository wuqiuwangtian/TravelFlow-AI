"""仅把明确的短回答补回原操作；新话题、假设和疑问不会隐式执行旧请求。"""

import re

from app.domain.clarification import JourneyClarification
from app.domain.itinerary import Itinerary
from app.domain.models import Requirements
from app.services.journey_messages import (
    JourneyMessage,
    itinerary_fingerprint,
    prepare_journey_message,
)
from app.services.replan_service import explicit_day
from app.services.travel_messages import AMOUNT_PATTERN, _number

DAY = r"第\s*(?:\d+|[一二三四五六七八九十两]+)\s*天"
STATION = r"第\s*(?:\d+|[一二三四五六七八九十两]+)\s*站"
CANCEL = re.compile(
    r"^(?:取消|算了|不用了|不改了|先不记了|取消这笔|取消这次操作|取消待确认操作)[。！!\s]*$"
)


def resume_journey_message(
    text: str, pending: JourneyClarification, itinerary: Itinerary, requirements: Requirements
) -> JourneyMessage | None:
    if pending.itinerary_fingerprint != itinerary_fingerprint(itinerary):
        return None
    answer = re.sub(r"^(?:是|选|就|指的是|说的是)\s*", "", text.strip()).rstrip("。！! ")
    clauses = list(pending.clauses)
    scopes = list(pending.day_scopes)
    selections = dict(pending.expense_selections)
    part = clauses[pending.index]
    if pending.kind == "expense":
        match = re.fullmatch(r"第\s*(\d+|[一二三四五六七八九十两]+)\s*(?:笔|条|个)", answer)
        if text.strip() in ("上一页消费", "下一页消费"):
            direction = 1 if text.strip() == "下一页消费" else -1
            page = max(
                0, min((len(pending.expense_options) - 1) // 8, pending.expense_page + direction)
            )
            updated = pending.model_copy(update={"expense_page": page})
            start = page * 8
            options = "\n".join(
                f"{i}. {target.label}"
                for i, target in enumerate(pending.expense_options[start : start + 8], start + 1)
            )
            return JourneyMessage(replies=[updated.prompt + "\n" + options], clarification=updated)
        if not match:
            return None
        index = _number(match[1]) - 1
        if not 0 <= index < len(pending.expense_options):
            return JourneyMessage(
                replies=["这个编号不在候选中，请选择列表里的消费，或取消这次操作。"],
                clarification=pending,
            )
        selections[pending.index] = pending.expense_options[index]
    elif pending.kind == "day" and re.fullmatch(DAY + r"(?:的)?(?:那一笔|那笔|这笔)?", answer):
        scopes[pending.index] = explicit_day(answer)
        part = re.sub(DAY, "", part).strip("，, ")
    elif pending.kind == "activity" and re.fullmatch(DAY + r"\s*" + STATION, answer):
        scopes[pending.index] = explicit_day(answer)
        part = re.sub(DAY + "|" + STATION, "", part).strip("，, ")
        part = answer + part
    elif pending.kind == "amount" and re.fullmatch(AMOUNT_PATTERN, answer, re.I):
        if re.search(AMOUNT_PATTERN, part, re.I):
            part = re.sub(AMOUNT_PATTERN, lambda _: answer, part, count=1, flags=re.I)
        else:
            part += answer
    else:
        return None
    clauses[pending.index] = part
    return prepare_journey_message(
        "",
        itinerary,
        requirements,
        clauses=clauses,
        day_scopes=scopes,
        expense_selections=selections,
    )
