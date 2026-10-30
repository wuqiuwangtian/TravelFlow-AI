"""旅中起点、预约保护和时间窗口约束，不让模型改写已经发生的行程。"""

import re
from datetime import datetime
from uuid import uuid4

from app.core.errors import InvalidRequest
from app.domain.itinerary import (
    Booking,
    DayPlan,
    Itinerary,
    ReplanContext,
    ReplanInput,
    ScheduledActivity,
)
from app.maps.matching import normal_name
from app.services.travel_time import minutes, time_label, window_error


def record_booking(
    itinerary: Itinerary, day_number: int, activity_id: str, booking: Booking | None
) -> None:
    day = next((d for d in itinerary.days if d.day == day_number), None)
    activity = next((a for a in day.activities if a.id == activity_id), None) if day else None
    if activity is None or activity.is_context_anchor:
        raise InvalidRequest("请选择实际停留点记录预约")
    if activity.progress != "not_started":
        raise InvalidRequest("该活动已有执行记录，请先恢复未开始后再更改预约")
    activity.booking = booking
    activity.time_is_suggested = booking is None
    if booking:
        activity.start_time, activity.start_day_offset, activity.duration_minutes = (
            booking.start_time,
            booking.start_day_offset,
            booking.duration_minutes,
        )


def set_replan_context(itinerary: Itinerary, day_number: int, data: ReplanInput) -> DayPlan:
    day = next((d for d in itinerary.days if d.day == day_number), None)
    if day is None:
        raise InvalidRequest("未找到当天行程")
    error = window_error(data.time, data.start_day_offset, data.end_time, data.end_day_offset)
    if error:
        raise InvalidRequest(error)
    anchor = next((a for a in day.activities if a.id == data.activity_id), None)
    if data.activity_id and anchor is None:
        raise InvalidRequest("出发地点不在当天行程中")
    if anchor is None and data.location_name.strip():

        def core(name: str) -> str:
            return re.sub(r"(?:风景名胜区|风景区|景区|博物院|公园)$", "", normal_name(name))

        names = [
            a
            for a in day.activities
            if data.location_name in (a.title, a.place_name)
            or a.place
            and a.place.name == data.location_name
            or len(core(data.location_name)) >= 2
            and core(data.location_name) == core(a.place_name)
        ]
        if len(names) == 1:
            anchor = names[0]
    if anchor is None:
        if not data.location_name.strip():
            raise InvalidRequest("请告诉我当前地点，或在线路中选择从哪一站出发")
        anchor = ScheduledActivity(
            id="position-" + uuid4().hex[:12],
            title=f"当前位置：{data.location_name}",
            place_name=data.location_name.strip(),
            city=data.city,
            is_context_anchor=True,
            duration_minutes=0,
            cost=0,
            cost_basis="total",
            cost_source="manual",
            start_time=data.time,
            start_day_offset=data.start_day_offset,
            progress="in_progress",
            progress_updated_at=datetime.now(),
            notes="用户描述的出发位置，不代表额外游玩停留。",
        )
        # 保留已经记录的历史；新的出发位置位于之后。
        prefix = [
            a
            for a in day.activities
            if a.progress != "not_started"
            and (not a.is_context_anchor or a.progress in {"completed", "skipped"})
        ]
        rest = [
            a for a in day.activities if a.progress == "not_started" and not a.is_context_anchor
        ]
        day.activities = prefix + [anchor] + rest
    elif (
        anchor.booking
        and anchor.progress == "not_started"
        and minutes(data.time, data.start_day_offset)
        < minutes(anchor.booking.start_time, anchor.booking.start_day_offset)
        + anchor.booking.duration_minutes
    ):
        # 人已到预约地点不代表预约已结束；另设零停留起点，原预约仍在后续约束中。
        booked = anchor
        day.activities = [
            a
            for a in day.activities
            if not a.is_context_anchor or a.progress in {"completed", "skipped"}
        ]
        index = next(i for i, a in enumerate(day.activities) if a.id == booked.id)
        anchor = ScheduledActivity(
            id="position-" + uuid4().hex[:12],
            title=f"当前位置：{booked.place_name or booked.title}",
            place_name=booked.place_name,
            city=booked.city,
            place=booked.place,
            place_status=booked.place_status,
            start_time=data.time,
            start_day_offset=data.start_day_offset,
            duration_minutes=0,
            cost=0,
            cost_basis="total",
            is_context_anchor=True,
            progress="in_progress",
            notes="出发位置，原预约仍作为后续固定安排保留。",
        )
        day.activities.insert(index, anchor)
    if data.end_activity_id and not any(a.id == data.end_activity_id for a in day.activities):
        raise InvalidRequest("结束地点不在当天行程中")
    if data.end_activity_id == anchor.id:
        raise InvalidRequest("出发与结束地点相同时，请分别设置抵达活动")
    if data.end_activity_id:
        end_index = next(i for i, a in enumerate(day.activities) if a.id == data.end_activity_id)
        anchor_index = next(i for i, a in enumerate(day.activities) if a.id == anchor.id)
        if end_index <= anchor_index:
            raise InvalidRequest("结束地点需位于出发地点之后")
    if anchor.is_planning_origin:
        anchor.is_planning_origin = False
        anchor.title = f"当前位置：{anchor.place_name}"
        anchor.progress, anchor.progress_updated_at = "in_progress", datetime.now()
    day.replan_context = ReplanContext(
        activity_id=anchor.id,
        time=data.time,
        start_day_offset=data.start_day_offset,
        end_time=data.end_time,
        end_day_offset=data.end_day_offset,
        end_activity_id=data.end_activity_id,
    )
    return day


def _preserve_replan_day(
    previous: Itinerary, current: Itinerary, day_number: int, *, preserve_order: bool = False
) -> DayPlan:
    old_day = next(d for d in previous.days if d.day == day_number)
    updated = next((d for d in current.days if d.day == day_number), old_day.model_copy(deep=True))
    context = old_day.replan_context
    updated.replan_context = context.model_copy(deep=True) if context else None
    if context:
        index = next(i for i, a in enumerate(old_day.activities) if a.id == context.activity_id)
        completed_after = [
            a for a in old_day.activities[index + 1 :] if a.progress != "not_started"
        ]
        prefix = [*old_day.activities[:index], *completed_after, old_day.activities[index]]
        prefix_ids = {a.id for a in prefix}
        prefix_names = {(a.title, a.place_name) for a in prefix}
        remaining = [
            a
            for a in updated.activities
            if a.id not in prefix_ids
            and (preserve_order or (a.title, a.place_name) not in prefix_names)
            and a.progress == "not_started"
            and not a.is_context_anchor
        ]
        # 模型遗漏的预约与结束地点由程序恢复。
        for old in old_day.activities[index + 1 :]:
            if old.id in prefix_ids:
                continue
            if old.booking or old.id == context.end_activity_id:
                found = next(
                    (
                        i
                        for i, a in enumerate(remaining)
                        if a.id == old.id
                        or (
                            not preserve_order
                            and a.place_name == old.place_name
                            and a.city == old.city
                            and bool(old.place_name)
                        )
                    ),
                    None,
                )
                if found is None:
                    remaining.append(old.model_copy(deep=True))
                else:
                    remaining[found] = old.model_copy(deep=True)
        if not preserve_order:
            remaining.sort(
                key=lambda a: (
                    minutes(a.booking.start_time, a.booking.start_day_offset)
                    if a.booking
                    else minutes(a.start_time, a.start_day_offset)
                    if a.start_time
                    else 10**9
                )
            )
        if context.end_activity_id:
            remaining = [a for a in remaining if a.id != context.end_activity_id] + [
                a for a in remaining if a.id == context.end_activity_id
            ]
        updated.activities = [a.model_copy(deep=True) for a in prefix] + remaining
        updated.legs = [
            leg.model_copy(deep=True)
            for leg in old_day.legs
            if leg.from_id in prefix_ids and leg.to_id in prefix_ids
        ]
    return updated


def preserve_replan_scope(
    previous: Itinerary,
    current: Itinerary,
    day_number: int | set[int],
    *,
    preserve_order: bool = False,
) -> None:
    """只采用指定日，其他天逐项保留；各日仍保护既有起点、预约和执行记录。"""
    numbers = {day_number} if isinstance(day_number, int) else day_number
    selected = {
        number: _preserve_replan_day(previous, current, number, preserve_order=preserve_order)
        for number in numbers
    }
    current.days = [
        selected[d.day] if d.day in selected else d.model_copy(deep=True) for d in previous.days
    ]


def optional_to_remove(day: DayPlan) -> int | None:
    """按最早可达时刻检查；只删起点之后未预约、未执行且非结束点的普通停留。"""
    context = day.replan_context
    if context is None:
        return None
    start = next((i for i, a in enumerate(day.activities) if a.id == context.activity_id), None)
    if start is None:
        return None
    cursor = minutes(context.time, context.start_day_offset)
    optional: list[int] = []
    for index in range(start + 1, len(day.activities)):
        activity = day.activities[index]
        if index - 1 < len(day.legs):
            cursor += int((day.legs[index - 1].duration_seconds or 0) + 59) // 60
        fixed_time = activity.booking.start_time if activity.booking else activity.start_time
        fixed_offset = (
            activity.booking.start_day_offset if activity.booking else activity.start_day_offset
        )
        if fixed_time and (activity.booking or not activity.time_is_suggested):
            fixed = minutes(fixed_time, fixed_offset)
            if cursor > fixed and optional:
                return optional[-1]
            cursor = max(cursor, fixed)
        elif (
            activity.progress == "not_started"
            and activity.id != context.end_activity_id
            and activity.id not in context.protected_activity_ids
            and (
                not context.preserve_logistics
                or activity.kind not in {"hotel", "station", "transport"}
            )
        ):
            optional.append(index)
        if activity.id != context.end_activity_id:
            cursor += activity.duration_minutes or 0
        if (
            context.end_time
            and cursor > minutes(context.end_time, context.end_day_offset)
            and optional
        ):
            return optional[-1]
    return None


def context_warnings(day: DayPlan) -> list[str]:
    context = day.replan_context
    if not context:
        return []
    start = next((i for i, a in enumerate(day.activities) if a.id == context.activity_id), 0)
    cursor = minutes(context.time, context.start_day_offset)
    warnings = []
    for earlier in day.activities[: start + 1]:
        if (
            earlier.booking
            and earlier.progress == "not_started"
            and minutes(earlier.booking.start_time, earlier.booking.start_day_offset)
            < minutes(context.time, context.start_day_offset)
        ):
            warnings.append(
                f"前段预约「{earlier.title}」尚未记录完成，已过原预约时刻，请核对实际情况。"
            )
    unknown = False
    for index in range(start + 1, len(day.activities)):
        activity = day.activities[index]
        leg = day.legs[index - 1] if index - 1 < len(day.legs) else None
        unknown = unknown or leg is None or leg.duration_seconds is None
        cursor += int(((leg.duration_seconds if leg else 0) or 0) + 59) // 60
        fixed_time = activity.booking.start_time if activity.booking else activity.start_time
        fixed_offset = (
            activity.booking.start_day_offset if activity.booking else activity.start_day_offset
        )
        if fixed_time and (activity.booking or not activity.time_is_suggested):
            fixed = minutes(fixed_time, fixed_offset)
            if cursor > fixed:
                label = "预约冲突" if activity.booking else "固定安排冲突"
                warnings.append(
                    f"{label}：「{activity.title}」预计无法在"
                    f"{time_label(fixed_time, fixed_offset)}"
                    "前抵达；固定时间保持不变。"
                )
            cursor = max(cursor, fixed)
        if activity.id != context.end_activity_id:
            cursor += activity.duration_minutes or 0
    if context.end_time and cursor > minutes(context.end_time, context.end_day_offset):
        deadline = time_label(context.end_time, context.end_day_offset)
        warnings.append(f"时间冲突：保留的必要安排仍超过{deadline}，请调整结束时间或明确更改预约。")
    if unknown:
        warnings.append("部分交通耗时尚未查到，时间窗口仅按已知耗时估算，需预留接驳时间。")
    return warnings


def explicit_day(text: str) -> int | None:
    from app.services.travel_messages import _number

    match = re.search(r"第\s*(\d+|[一二三四五六七八九十两]+)\s*天", text)
    return _number(match[1]) if match else None


def explicit_days(text: str) -> set[int]:
    """普通修改明确提到的行程日；住宿的第N晚归入第N天。"""
    from app.services.travel_messages import _number

    return {
        _number(value)
        for value in re.findall(r"第\s*(\d+|[一二三四五六七八九十两]+)\s*(?:天|晚)", text)
    }


def check_overnight_links(itinerary: Itinerary) -> None:
    """仅提示相邻行程日的重叠，不能擅改次日预约或历史。"""
    for day in itinerary.days:
        day.warnings = [w for w in day.warnings if not w.startswith("跨日衔接冲突：")]
        ends = [
            (
                (day.day - 1) * 1440
                + minutes(a.start_time, a.start_day_offset)
                + (a.duration_minutes or 0),
                a,
            )
            for a in day.activities
            if a.start_time and a.progress != "skipped"
        ]
        if not ends:
            continue
        finish, last = max(ends, key=lambda item: item[0])
        if finish <= day.day * 1440:
            continue
        following = [
            ((other.day - 1) * 1440 + minutes(a.start_time, a.start_day_offset), other.day, a)
            for other in itinerary.days
            if other.day > day.day
            for a in other.activities
            if a.start_time and a.progress != "skipped"
        ]
        overlaps = [entry for entry in following if entry[0] < finish]
        if overlaps:
            _, number, first = min(overlaps, key=lambda item: item[0])
            day.warnings.append(
                f"跨日衔接冲突：本日「{last.title}」延续到次日后，与第{number}天"
                f"「{first.title}」的{time_label(first.start_time or '', first.start_day_offset)}"
                "安排重叠；"
                "其他天和已有预约未自动改动，请核对衔接时间。"
            )
