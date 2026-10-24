"""工程内的确定性筛选与几何顺序建议，不调用模型或改写正式旅程。"""

import math
import re

from app.core.errors import InvalidRequest
from app.domain.itinerary import DayPlan, RouteLeg, ScheduledActivity
from app.domain.studio_data import (
    RouteOrderReport,
    SelectionDecision,
    SelectionReport,
    StudioPlanData,
)
from app.domain.workbench import StudioNode
from app.maps.matching import distance_meters, normal_name
from app.services.itinerary_service import summarise_day
from app.services.workflow_rerun import protected_ids


def keywords(value: str) -> list[str]:
    values = list(
        dict.fromkeys(item.strip() for item in re.split(r"[\n,，;；、]+", value) if item.strip())
    )
    if len(values) > 20 or any(len(item) > 100 for item in values):
        raise InvalidRequest("必去或排除名单最多20项，每项最多100字，请用换行或逗号分隔。")
    if any(not normal_name(item) for item in values):
        raise InvalidRequest("名单需要填写具体名称或关键词，不能只有标点。")
    return values


def matches(activity: ScheduledActivity, words: list[str]) -> bool:
    names = [normal_name(activity.place_name), normal_name(activity.title)]
    return any(normal_name(word) in name for word in words for name in names)


def hard_ids(day: DayPlan) -> set[str]:
    ids = protected_ids(day)
    ids.update(a.id for a in day.activities if a.cost_source == "manual")
    manual_targets = {
        leg.to_id
        for leg in day.legs
        if leg.cost_source == "manual" or leg.mode in {"rail", "flight", "transfer"}
    }
    for index, activity in enumerate(day.activities):
        if activity.id in manual_targets or activity.transport_mode in {
            "rail",
            "flight",
            "transfer",
        }:
            ids.add(activity.id)
            if index:
                ids.add(day.activities[index - 1].id)
    return ids


def clear_changed_transport(day: DayPlan, original: DayPlan) -> None:
    previous = {
        a.id: original.activities[i - 1].id if i else None
        for i, a in enumerate(original.activities)
    }
    for index, activity in enumerate(day.activities):
        before = day.activities[index - 1].id if index else None
        if previous.get(activity.id) != before:
            activity.transport_minutes = None
            activity.transport_cost = None
            activity.transport_cost_basis = "unknown"
            activity.transport_source_indices = []
    apply_logistics(day, original)
    old_legs = {(leg.from_id, leg.to_id): leg for leg in original.legs}
    day.legs = []
    for origin, target in zip(day.activities, day.activities[1:], strict=False):
        old = old_legs.get((origin.id, target.id))
        if old and (
            old.cost_source == "manual"
            or old.mode in {"rail", "flight", "transfer"}
            or target.progress != "not_started"
        ):
            day.legs.append(old.model_copy(deep=True))
        else:
            day.legs.append(
                RouteLeg(
                    from_id=origin.id,
                    to_id=target.id,
                    mode=target.transport_mode,
                    status="planned" if target.transport_minutes is not None else "unavailable",
                    duration_seconds=target.transport_minutes * 60
                    if target.transport_minutes is not None
                    else None,
                    cost=target.transport_cost,
                    cost_basis=target.transport_cost_basis,
                    cost_source="reference"
                    if target.transport_source_indices
                    else "estimate"
                    if target.transport_cost is not None
                    else "unknown",
                    source_indices=target.transport_source_indices[:],
                )
            )
    day.warnings = []
    day.checked_at = None
    day.schedule_notes = []


def apply_logistics(day: DayPlan, original: DayPlan) -> None:
    old_legs = {(leg.from_id, leg.to_id): leg for leg in original.legs}
    for origin, target in zip(day.activities, day.activities[1:], strict=False):
        old = old_legs.get((origin.id, target.id))
        if old and old.mode in {"rail", "flight", "transfer"}:
            target.transport_mode = old.mode
            target.transport_minutes = (
                math.ceil(old.duration_seconds / 60) if old.duration_seconds is not None else None
            )
            target.transport_cost = old.cost
            target.transport_cost_basis = old.cost_basis
            target.transport_source_indices = old.source_indices[:]


def select_places(node: StudioNode, plan: StudioPlanData) -> StudioPlanData:
    required = keywords(node.parameters.required_places)
    excluded = keywords(node.parameters.excluded_places)
    attractions = [a for d in plan.days for a in d.activities if a.kind == "attraction"]
    missing = [word for word in required if not any(matches(a, [word]) for a in attractions)]
    if missing:
        raise InvalidRequest(
            "上游没有找到必去景点：" + "、".join(missing) + "。请先在上游补充具体地点。"
        )
    if any(matches(a, required) and matches(a, excluded) for a in attractions):
        raise InvalidRequest("必去与排除名单命中了同一景点，请调整名单后重跑筛选节点。")
    result = plan.model_copy(deep=True)
    report = SelectionReport(
        node_title=node.title, before_count=sum(len(d.activities) for d in plan.days), after_count=0
    )
    changed = False
    for day, original in zip(result.days, plan.days, strict=True):
        protected = hard_ids(day)
        priority = {
            a.id
            for a in day.activities
            if a.kind == "attraction" and (a.id in protected or matches(a, required))
        }
        limit = node.parameters.max_attractions_per_day
        remaining = max(0, limit - len(priority)) if limit is not None else 80
        kept = []
        for activity in day.activities:
            reason, action = "", "kept"
            if activity.kind != "attraction":
                kept.append(activity)
                continue
            if activity.id in protected:
                reason = "固定安排、已执行记录、人工费用或交通衔接保留"
            elif activity.id in priority:
                reason = "命中必去名单"
            elif matches(activity, excluded):
                action, reason = "removed", "命中排除名单"
            elif remaining <= 0:
                action, reason = "removed", "超出每日景点上限，按上游顺序取舍"
            else:
                remaining -= 1
            if reason:
                report.decisions.append(
                    SelectionDecision(
                        day=day.day,
                        activity_id=activity.id,
                        title=activity.place_name or activity.title,
                        action=action,
                        reason=reason,
                    )
                )
            if action == "removed":
                continue
            if node.parameters.attraction_stay_minutes is not None and activity.id not in protected:
                duration = node.parameters.attraction_stay_minutes
                if activity.duration_minutes != duration:
                    report.decisions.append(
                        SelectionDecision(
                            day=day.day,
                            activity_id=activity.id,
                            title=activity.place_name or activity.title,
                            action="duration",
                            reason=f"建议停留{activity.duration_minutes or '未知'}"
                            f" → {duration}分钟",
                        )
                    )
                    activity.duration_minutes = duration
            kept.append(activity)
        if not kept:
            raise InvalidRequest(f"第{day.day}天筛选后没有活动，请放宽排除名单或调整上游安排。")
        day.activities = kept
        day_changed = day.activities != original.activities
        if day_changed:
            clear_changed_transport(day, original)
            summarise_day(day, result.requirements)
        changed = changed or day_changed
        if limit is not None and len(priority) > limit:
            result.warnings.append(
                f"第{day.day}天必去与受保护景点共{len(priority)}处，超过上限{limit}；已完整保留。"
            )
    if changed:
        result.route_status = "unchecked"
        result.optimization = []
        result.warnings.append("景点或停留时长已调整，请连接路线校验节点重新核对交通与时间。")
    report.after_count = sum(len(d.activities) for d in result.days)
    result.selection = report
    result.warnings = list(dict.fromkeys(result.warnings))
    return result


def optimize_day(day: DayPlan) -> tuple[DayPlan, RouteOrderReport]:
    result = day.model_copy(deep=True)
    report = RouteOrderReport(
        day=day.day,
        before=[a.place_name or a.title for a in day.activities],
        after=[],
        reason="未找到更短的直线顺序，沿用上游。",
    )
    if not day.activities:
        report.after = []
        return result, report
    fixed = (
        hard_ids(day)
        | {day.activities[0].id}
        | {a.id for a in day.activities if a.kind != "attraction"}
    )
    skipped = False
    distances: dict[tuple[str, str], float] = {}

    def distance(a: ScheduledActivity, b: ScheduledActivity) -> float:
        key = (a.id, b.id) if a.id <= b.id else (b.id, a.id)
        if key not in distances:
            assert a.place is not None and b.place is not None
            distances[key] = distance_meters(a.place, b.place)
        return distances[key]

    def length(items: list[ScheduledActivity]) -> float:
        return sum(distance(a, b) for a, b in zip(items, items[1:], strict=False))

    start = 0
    for end in [i for i, a in enumerate(day.activities) if a.id in fixed] + [len(day.activities)]:
        block = result.activities[start:end]
        left = result.activities[start - 1 : start] if start else []
        right = result.activities[end : end + 1]
        route = [*left, *block, *right]
        if len(block) > 1:
            cities = {
                normal_name((a.place.city if a.place else "") or a.city).removesuffix("市")
                for a in route
            }
            if (
                any(a.place is None or a.place_status != "matched" for a in route)
                or len(cities - {""}) > 1
            ):
                skipped = True
            else:
                # 有确认坐标的区段才搜索；保留固定端点，且只接受总直线长度严格改善。
                best = block[:]
                best_length = length([*left, *best, *right])
                for _ in range(4):
                    improved = False
                    for i in range(len(best) - 1):
                        for j in range(i + 1, len(best)):
                            candidate = best[:i] + list(reversed(best[i : j + 1])) + best[j + 1 :]
                            cost = length([*left, *candidate, *right])
                            if cost + 1 < best_length:
                                best, best_length, improved = candidate, cost, True
                    if not improved:
                        break
                result.activities[start:end] = best
        start = end + 1
    report.changed = [a.id for a in result.activities] != [a.id for a in day.activities]
    report.after = [a.place_name or a.title for a in result.activities]
    if all(a.place is not None and a.place_status == "matched" for a in day.activities):
        report.before_meters, report.after_meters = (
            round(length(day.activities)),
            round(length(result.activities)),
        )
    if report.changed:
        report.reason = "在固定站点之间减少直线折返；不代表实际路程或用时一定更短。"
    if skipped:
        report.reason += " 部分区段缺少确认坐标或跨城市，已沿用原顺序。"
    return result, report


def restore_fixed(day: DayPlan, original: DayPlan) -> None:
    protected = hard_ids(original)
    by_id = {a.id: a for a in original.activities}
    for index, activity in enumerate(day.activities):
        if activity.id in protected:
            restored = by_id[activity.id].model_copy(deep=True)
            if (
                restored.time_is_suggested
                and restored.progress == "not_started"
                and not restored.booking
            ):
                restored.start_time = activity.start_time
                restored.start_day_offset = activity.start_day_offset
                if restored.duration_minutes is None:
                    restored.duration_minutes = activity.duration_minutes
            if restored.place is None:
                for key in ("place", "place_status", "candidates", "match_reason"):
                    setattr(restored, key, getattr(activity, key))
            day.activities[index] = restored
    apply_logistics(day, original)
    manual = {(leg.from_id, leg.to_id): leg for leg in original.legs if leg.cost_source == "manual"}
    all_legs = {(leg.from_id, leg.to_id): leg for leg in original.legs}
    for index, leg in enumerate(day.legs):
        historical = all_legs.get((leg.from_id, leg.to_id))
        if historical and by_id[leg.to_id].progress != "not_started":
            day.legs[index] = historical.model_copy(deep=True)
            continue
        old = manual.get((leg.from_id, leg.to_id))
        if old:
            leg.cost, leg.cost_basis, leg.cost_source = old.cost, old.cost_basis, old.cost_source
            leg.source_indices = old.source_indices[:]
