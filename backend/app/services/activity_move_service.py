"""跨天搬移保留活动身份，只有计划预算的日期随之变化。"""

from app.core.errors import InvalidRequest
from app.domain.itinerary import Itinerary
from app.domain.linked_replan import LinkedReplanInput


def apply_activity_moves(candidate: Itinerary, data: LinkedReplanInput) -> list[str]:
    """只修改尚未保存的草稿；调用者任一步失败都丢弃整份草稿。"""
    ids = [move.activity_id for move in data.moves]
    if len(ids) != len(set(ids)):
        raise InvalidRequest("同一个景点一次只能搬移一次，请确认目标日期。")
    days = {d.day: d for d in candidate.days}
    previous = {
        a.id: day.activities[i - 1].id if i else None
        for day in candidate.days
        for i, a in enumerate(day.activities)
    }
    notes = []
    for move in data.moves:
        if {move.source_day, move.destination_day} != {data.first_day, data.first_day + 1}:
            raise InvalidRequest("景点搬移目前只支持相邻两天，请明确原日期和目标日期。")
        source, destination = days.get(move.source_day), days.get(move.destination_day)
        if source is None or destination is None:
            raise InvalidRequest("搬移日期不在当前行程中。")
        matches = [a for d in candidate.days for a in d.activities if a.id == move.activity_id]
        if len(matches) != 1 or not any(a.id == move.activity_id for a in source.activities):
            raise InvalidRequest("要搬移的景点已发生变化，请重新选择。")
        activity = matches[0]
        if activity.progress != "not_started" or activity.is_context_anchor:
            raise InvalidRequest("已经开始、已完成、已跳过或作为出发位置的活动不能跨天搬移。")
        if activity.booking or not activity.time_is_suggested:
            raise InvalidRequest("这个项目已有预约或固定时刻，不能自动更换日期。请先核对原预约。")
        if activity.kind in {"hotel", "station", "transport"}:
            raise InvalidRequest("住宿、车站和交通安排暂不跨天搬移，避免改动行程的固定衔接。")
        index = next(i for i, a in enumerate(source.activities) if a.id == activity.id)
        if source.replan_context:
            if source.replan_context.end_activity_id == activity.id:
                raise InvalidRequest("这个地点是当天指定的结束地点，请先调整结束安排再搬移。")
            anchor_index = next(
                (
                    i
                    for i, a in enumerate(source.activities)
                    if a.id == source.replan_context.activity_id
                ),
                -1,
            )
            if index <= anchor_index:
                raise InvalidRequest("这个地点在当前出发位置之前，不能通过搬移改写前段行程。")
        city = (activity.city or (activity.place.city if activity.place else "")).removesuffix("市")
        cities = {
            (a.city or (a.place.city if a.place else "")).removesuffix("市")
            for a in destination.activities
            if not a.is_context_anchor
        }
        cities.discard("")
        if city and cities and cities != {city}:
            raise InvalidRequest(
                "这次搬移涉及不同城市，需要同时安排城际交通；目前先支持同城相邻两天。"
            )
        prefix = max(
            (
                i
                for i, a in enumerate(destination.activities)
                if a.progress != "not_started"
                or a.is_context_anchor
                or (destination.replan_context and a.id == destination.replan_context.activity_id)
            ),
            default=-1,
        )
        if move.before_activity_id:
            position = next(
                (
                    i
                    for i, a in enumerate(destination.activities)
                    if a.id == move.before_activity_id
                ),
                None,
            )
            if position is None:
                raise InvalidRequest("目标停留点已发生变化，请重新说明搬移顺序。")
        else:
            position = next(
                (
                    i
                    for i, a in enumerate(destination.activities)
                    if i > prefix
                    and a.progress == "not_started"
                    and not a.is_context_anchor
                    and a.kind not in {"hotel", "station", "transport"}
                ),
                len(destination.activities),
            )
            if position == len(destination.activities):
                position = next(
                    (
                        i
                        for i, a in enumerate(destination.activities)
                        if i > prefix and a.progress == "not_started" and not a.is_context_anchor
                    ),
                    position,
                )
        if position <= prefix or any(
            a.progress != "not_started" or a.is_context_anchor
            for a in destination.activities[position:]
        ):
            raise InvalidRequest("目标位置在已有执行记录之前，请选择后续未开始的位置。")
        if len(destination.activities) >= 79:
            raise InvalidRequest("目标日期的停留点已接近上限，请先精简。")
        source.activities.pop(index)
        activity.start_time, activity.start_day_offset = None, 0
        destination.activities.insert(position, activity)
        for item in candidate.budget_items:
            if item.activity_id == activity.id or item.id in {
                "stay-" + activity.id,
                "leg-" + activity.id,
            }:
                item.day = destination.day
        notes.append(f"「{activity.title}」从第{source.day}天移至第{destination.day}天")
    if not data.moves:
        return notes
    # 抵达地点的旧交通估算只适用于旧起点；失效连线必须重新查，不能照搬耗时或票价。
    for day in candidate.days:
        if day.day not in {data.first_day, data.first_day + 1}:
            continue
        for index, activity in enumerate(day.activities):
            origin = day.activities[index - 1].id if index else None
            if previous.get(activity.id) == origin or activity.progress != "not_started":
                continue
            if activity.transport_mode in {"rail", "flight", "transfer"}:
                raise InvalidRequest(
                    "搬移会改变铁路、航班或专门接驳的起点，请先明确交通衔接，原计划未改变。"
                )
            activity.transport_minutes = None
            activity.transport_cost = None
            activity.transport_cost_basis = "unknown"
            activity.transport_source_indices = []
            activity.transport_mode = "auto"
        day.legs = [
            leg
            for leg in day.legs
            if any(
                previous.get(a.id) == (day.activities[i - 1].id if i else None)
                and leg.to_id == a.id
                and leg.from_id == previous.get(a.id)
                for i, a in enumerate(day.activities)
            )
        ]
    return notes
