"""只在内存中协调连续两天；冲突方案不能作为半份成功结果保存。"""

from uuid import uuid4

from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import InvalidRequest
from app.domain.itinerary import Itinerary, LinkedReplanNote, ReplanContext, ScheduledActivity
from app.domain.linked_replan import LinkedActivityChange, LinkedReplanInput, LinkedReplanPreview
from app.domain.models import Requirements
from app.services.itinerary_service import enrich_itinerary
from app.services.journey_messages import itinerary_fingerprint
from app.services.replan_service import preserve_replan_scope
from app.services.travel_time import minutes, time_label


def _clock(value: int) -> str:
    return f"{value % 1440 // 60:02d}:{value % 60:02d}"


def _finish(activity: ScheduledActivity) -> int:
    return minutes(activity.start_time or "00:00", activity.start_day_offset) + (
        activity.duration_minutes or 0
    )


async def preview_linked_replan(
    original: Itinerary,
    data: LinkedReplanInput,
    requirements: Requirements,
    settings: Settings,
    *,
    working: Itinerary | None = None,
) -> LinkedReplanPreview:
    candidate = (working or original).model_copy(deep=True)
    days = {d.day: d for d in candidate.days}
    number = data.first_day
    if number not in days or number + 1 not in days:
        raise InvalidRequest("需要旅程中连续的两天，请说明例如「联动调整第1天和第2天」。")
    first, second = days[number], days[number + 1]
    if any(a.progress != "not_started" and not a.is_context_anchor for a in second.activities):
        raise InvalidRequest(
            "次日已有执行记录，请从次日的实际当前位置单独调整，避免改写已发生的旅程。"
        )
    if not any(not a.is_context_anchor and a.progress != "skipped" for a in second.activities):
        raise InvalidRequest("次日没有可衔接的安排，请先补充次日行程。")
    protected = sorted({e.activity_id for e in original.expenses if e.activity_id})
    if first.replan_context:
        first.replan_context.preserve_logistics = True
        first.replan_context.protected_activity_ids = protected
        preserve_replan_scope(candidate.model_copy(deep=True), candidate, number)
        candidate = await enrich_itinerary(candidate, requirements, settings, day_number=number)
        first = next(d for d in candidate.days if d.day == number)
        second = next(d for d in candidate.days if d.day == number + 1)
    scheduled = [a for a in first.activities if a.progress != "skipped" and not a.is_context_anchor]
    if not scheduled or any(not a.start_time or a.duration_minutes is None for a in scheduled):
        raise InvalidRequest("前一天还有未确定的开始时间或停留时长，请先补充，才能推算两天衔接。")
    last = max(scheduled, key=_finish)
    if not last.place_name and not last.place:
        raise InvalidRequest("前一天的结束地点还不明确，请先补充具体地点，再衔接次日路线。")
    finish = _finish(last)
    if first.replan_context:
        current_time = minutes(first.replan_context.time, first.replan_context.start_day_offset)
        if current_time > finish:
            finish = current_time
            last = next(a for a in first.activities if a.id == first.replan_context.activity_id)
    ready = finish + data.rest_minutes
    old_starts = [
        minutes(a.start_time, a.start_day_offset)
        for a in second.activities
        if a.start_time and a.progress != "skipped" and not a.is_context_anchor
    ]
    old_departure = min(old_starts, default=9 * 60)
    departure = (
        1440 + minutes(data.next_departure)
        if data.next_departure
        else max(1440 + old_departure, ready)
    )
    conflicts = []
    if departure < ready:
        conflicts.append(
            f"第{number + 1}天{data.next_departure}出发早于预留休息结束时间"
            f"（第{number + ready // 1440}天{_clock(ready)}），请调整出发时间或休息时长。"
        )
    if departure >= 2880:
        raise InvalidRequest("本次已影响第三天，请缩短调整范围；目前只支持相邻两天统一调整。")
    anchor = ScheduledActivity(
        id="linked-position-" + uuid4().hex[:12],
        title=f"衔接起点：{last.place_name or last.title}",
        place_name=last.place_name,
        city=last.city,
        place=last.place.model_copy(deep=True) if last.place else None,
        place_status=last.place_status,
        is_context_anchor=True,
        progress="not_started",
        start_time=_clock(departure),
        duration_minutes=0,
        cost=0,
        cost_basis="total",
        cost_source="manual",
        notes="沿用前一天最后地点作为出发位置；不代表新增游玩或已完成活动。",
    )
    history = [
        a
        for a in second.activities
        if a.is_context_anchor and a.progress in {"completed", "skipped"}
    ]
    future = [a for a in second.activities if not a.is_context_anchor]
    if len(history) + len(future) >= 80:
        raise InvalidRequest("次日停留点已达到上限，请先精简后再加入两天衔接起点。")
    second.activities = history + [anchor] + future
    end = next((a for a in reversed(future) if a.kind in {"hotel", "station", "transport"}), None)
    old_context = second.replan_context
    end_time = old_context.end_time if old_context and old_context.end_time else "23:59"
    end_offset = old_context.end_day_offset if old_context and old_context.end_time else 0
    if minutes(end_time, end_offset) <= departure - 1440:
        conflicts.append(f"第{number + 1}天原结束期限{end_time}早于新的出发时刻，请先调整期限。")
        end_time, end_offset = "23:59", 0
    if _clock(departure) == "23:59":
        raise InvalidRequest("次日已没有可调整的时间，请缩短休息时间或重新安排这两天。")
    second.replan_context = ReplanContext(
        activity_id=anchor.id,
        time=anchor.start_time or "09:00",
        end_time=end_time,
        end_day_offset=end_offset,
        end_activity_id=old_context.end_activity_id if old_context else end.id if end else None,
        preserve_logistics=True,
        protected_activity_ids=protected,
    )
    candidate = await enrich_itinerary(candidate, requirements, settings, day_number=number + 1)
    first = next(d for d in candidate.days if d.day == number)
    second = next(d for d in candidate.days if d.day == number + 1)
    for day in (first, second):
        conflicts.extend(f"第{day.day}天：{w}" for w in day.warnings if "冲突" in w)
        origin = next(
            (
                i
                for i, a in enumerate(day.activities)
                if day.replan_context and a.id == day.replan_context.activity_id
            ),
            -1,
        )
        unknown = False
        for index in range(origin + 1, len(day.activities)):
            activity = day.activities[index]
            if index:
                unknown = (
                    unknown
                    or index - 1 >= len(day.legs)
                    or day.legs[index - 1].duration_seconds is None
                )
            if unknown and (activity.booking or not activity.time_is_suggested):
                conflicts.append(
                    f"第{day.day}天：固定项目「{activity.title}」之前有未核实的交通耗时，请先补充或校验交通后重新预览。"
                )
    if any(_finish(a) >= 1440 for a in second.activities if a.progress != "skipped"):
        conflicts.append(f"第{number + 1}天仍延续至第三天，本次不能只保存两天中的部分调整。")
    # 费用、附件和实际支付日期均不因时间调整被改写。
    candidate.expenses = [e.model_copy(deep=True) for e in original.expenses]
    candidate.adjustments = [a.model_copy(deep=True) for a in original.adjustments]
    candidate.budget_items = [b.model_copy(deep=True) for b in original.budget_items]
    candidate.days = [
        d
        if d.day in {number, number + 1}
        else next(old.model_copy(deep=True) for old in original.days if old.day == d.day)
        for d in candidate.days
    ]
    note = LinkedReplanNote(
        first_day=number,
        second_day=number + 1,
        rest_minutes=data.rest_minutes,
        next_departure=anchor.start_time or "09:00",
    )
    changes = []
    for day in candidate.days:
        if day.day not in {number, number + 1}:
            continue
        day.linked_replan = note.model_copy(deep=True)
        old = next(d for d in original.days if d.day == day.day)
        updated = {a.id: a for a in day.activities}
        for activity in old.activities:
            if activity.is_context_anchor:
                continue
            current = updated.get(activity.id)
            before = time_label(activity.start_time or "待定", activity.start_day_offset)
            after = (
                time_label(current.start_time or "待定", current.start_day_offset)
                if current
                else "本次精简"
            )
            changes.append(
                LinkedActivityChange(
                    day=day.day,
                    title=activity.title,
                    before=before,
                    after=after,
                    status="omitted" if not current else "kept" if before == after else "shifted",
                    protected=bool(
                        activity.booking
                        or not activity.time_is_suggested
                        or activity.id in protected
                        or activity.kind in {"hotel", "station", "transport"}
                        or activity.progress != "not_started"
                    ),
                )
            )
    warnings = list(
        dict.fromkeys(w for d in (first, second) for w in d.warnings if "冲突" not in w)
    )
    prompt = (
        f"第{number}—{number + 1}天联动预览，尚未保存。\n"
        f"按第{number}天{time_label(last.start_time or '', last.start_day_offset)}的"
        f"「{last.title}」结束后预留{data.rest_minutes / 60:g}小时休息，"
        f"第{number + 1}天{anchor.start_time}从「{last.place_name or last.title}」出发。\n"
        "预约、住宿、车站、已付款项目和实际账本保留；这里仍是建议时刻。"
    )
    if conflicts:
        prompt += "\n有未解决的时间冲突，请先调整条件；原计划和账目未改变。"
    else:
        prompt += "\n应用后两天、路线、预算与导出一起更新，只保存一个版本。"
    try:
        candidate = Itinerary.model_validate(candidate.model_dump())
    except ValidationError as exc:
        raise InvalidRequest(
            "方案超过行程支持的时间或停留点范围，原计划未改变，请缩短调整范围。"
        ) from exc
    return LinkedReplanPreview(
        itinerary=candidate,
        prompt=prompt,
        changes=changes,
        conflicts=list(dict.fromkeys(conflicts)),
        warnings=warnings,
        itinerary_fingerprint=itinerary_fingerprint(original),
    )
