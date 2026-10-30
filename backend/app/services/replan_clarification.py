"""用明确的行程日短答恢复原旅中请求，不重新猜地点、钟点或费用操作。"""

import re
from dataclasses import dataclass

from app.domain.clarification import ReplanClarification
from app.domain.itinerary import Itinerary
from app.domain.refinement import RefineIntent
from app.services.journey_messages import itinerary_fingerprint
from app.services.replan_service import explicit_day


@dataclass
class ReplanAnswer:
    reply: str = ""
    pending: ReplanClarification | None = None
    message: str = ""
    verdict: RefineIntent | None = None


def ask_replan_day(
    message: str,
    verdict: RefineIntent,
    itinerary: Itinerary,
    after_message_id: str,
    *,
    refinement: str = "",
) -> ReplanClarification:
    update = verdict.on_trip
    assert update is not None
    return ReplanClarification(
        message=message,
        refinement=refinement,
        verdict=verdict.model_copy(deep=True),
        days=[day.day for day in itinerary.days],
        prompt=(
            "请说明现在是旅程第几天。\n"
            f"已保留本次出发地点与{update.time}出发时刻、结束要求；"
            "直接回复「第2天」或选择日期即可继续。行程和账目尚未改变，"
            "也可以说「取消这次操作」。"
        ),
        itinerary_fingerprint=itinerary_fingerprint(itinerary),
        after_message_id=after_message_id,
    )


def resume_replan_day(
    text: str, pending: ReplanClarification, itinerary: Itinerary, after_message_id: str
) -> ReplanAnswer | None:
    answer = re.sub(r"^(?:就是|是|选择|选|就|指的是|说的是)\s*", "", text.strip())
    if not re.fullmatch(r"第\s*(?:\d+|[一二三四五六七八九十两]+)\s*天[。！!\s]*", answer):
        return None
    if pending.itinerary_fingerprint != itinerary_fingerprint(itinerary):
        return ReplanAnswer(
            reply="行程已变化，这次调整不能直接继续，请重新说明当前地点和出发时刻。"
        )
    day = explicit_day(answer)
    if day not in pending.days or not any(item.day == day for item in itinerary.days):
        updated = ask_replan_day(
            pending.message,
            pending.verdict,
            itinerary,
            after_message_id,
            refinement=pending.refinement,
        )
        updated.prompt = "这份行程没有这个日期，请选择已有的行程日。\n" + updated.prompt
        return ReplanAnswer(reply=updated.prompt, pending=updated)
    verdict = pending.verdict.model_copy(deep=True)
    assert verdict.on_trip is not None
    verdict.on_trip.day = day
    if pending.refinement and pending.message.endswith(pending.refinement):
        message = pending.message[: -len(pending.refinement)] + f"第{day}天，{pending.refinement}"
    else:
        message = f"第{day}天，{pending.message}"
    return ReplanAnswer(message=message, verdict=verdict)
