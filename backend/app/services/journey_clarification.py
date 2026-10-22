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
from app.services.travel_messages import AMOUNT_PATTERN

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
    part = clauses[pending.index]
    if pending.kind == "day" and re.fullmatch(DAY + r"(?:的)?(?:那一笔|那笔|这笔)?", answer):
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
    return prepare_journey_message("", itinerary, requirements, clauses=clauses, day_scopes=scopes)
