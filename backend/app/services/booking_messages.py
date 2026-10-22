"""记录用户明确说明的已有预约；不代订票、不改变外部预约。"""

import re

from app.core.errors import AppError
from app.domain.itinerary import Booking, Itinerary
from app.services.replan_service import explicit_day, record_booking
from app.services.travel_messages import TravelReply, _number
from app.services.travel_time import time_label


def handle_booking_message(text: str, itinerary: Itinerary) -> TravelReply | None:
    if re.search(
        r"如果|预计|打算|想预约|帮我预约|能否|是否|不要|不是|未预约|没预约|吗|[?？]", text
    ):
        return None
    clear = bool(re.search(r"清除预约记录|取消预约记录|删除预约记录", text))
    if not clear and not re.search(r"已预约|已经预约|预约好了|已经订好|已订好|记录已有预约", text):
        return None
    if re.search(r"花了|记账|支出|实付", text):
        return TravelReply("这句话包含预约和实付，请分别告诉我预约时间和实际消费，避免漏记。")
    day_number = explicit_day(text)
    ordinal = re.search(r"第\s*(\d+|[一二三四五六七八九十两]+)\s*站", text)
    candidates = [
        (day.day, a)
        for day in itinerary.days
        if day_number is None or day.day == day_number
        for index, a in enumerate(day.activities, 1)
        if (ordinal and index == _number(ordinal[1]))
        or (not ordinal and (a.title in text or a.place_name and a.place_name in text))
    ]
    if len(candidates) != 1:
        return TravelReply(
            "请说明哪一天的哪一站，例如「第2天第1站已预约16:00，停留60分钟」。",
            clarification="day"
            if len(candidates) > 1 and len({number for number, _ in candidates}) > 1
            else "activity",
        )
    number, activity = candidates[0]
    times = re.findall(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)", text)
    if not clear and len(times) != 1:
        return TravelReply("请给出这项已有预约的开始时间，例如「第2天第1站已预约16:00」。")
    duration = re.search(r"(?:停留|时长|持续)\s*(\d+)\s*分钟", text)
    duration_minutes = int(duration[1]) if duration else activity.duration_minutes or 60
    if not 1 <= duration_minutes <= 1440:
        return TravelReply("预约停留时长应在1到1440分钟之间，请重新说明。")
    result = itinerary.model_copy(deep=True)
    try:
        booking = (
            None
            if clear
            else Booking(
                start_time=f"{int(times[0][0]):02d}:{times[0][1]}",
                start_day_offset=(
                    1
                    if re.search(
                        r"(?:次日|翌日|第二天|明天)\s*(?:凌晨|早上|上午|下午|晚上)?\s*\d{1,2}[:：]",
                        text,
                    )
                    else 0
                    if re.search(r"当天|当日", text)
                    else activity.booking.start_day_offset
                    if activity.booking
                    else 0
                ),
                duration_minutes=duration_minutes,
            )
        )
        record_booking(result, number, activity.id, booking)
    except AppError as exc:
        return TravelReply(exc.message)
    if booking is None:
        reply = "已清除这项预约的行程约束记录；外部预约没有取消。"
    else:
        reply = (
            f"已记录第{number}天「{activity.title}」的已有预约："
            f"{time_label(booking.start_time, booking.start_day_offset)}，"
            f"停留{booking.duration_minutes}分钟。后续调整会保留此预约。"
        )
    return TravelReply(reply, result)
