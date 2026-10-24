"""连续行程：自动定位、分段交通、建议时间排程与统一费用计算。"""

from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime
from uuid import uuid4

import httpx

from app.core.config import Settings
from app.core.errors import ProviderError
from app.domain.itinerary import (
    DayDraft,
    DayPlan,
    Itinerary,
    RouteLeg,
    ScheduledActivity,
    TravelMode,
)
from app.domain.models import Requirements
from app.maps.amap import AmapProvider
from app.maps.base import MapProvider
from app.maps.matching import choose_place, distance_meters, normal_name
from app.services.travel_time import minutes as _minutes
from app.services.travel_time import time_label

MODE_LABELS = {
    "auto": "智能衔接",
    "walking": "步行",
    "transit": "公共交通",
    "driving": "驾车/打车",
    "rail": "铁路",
    "flight": "航班",
    "transfer": "接驳",
}


def _kind(activity: ScheduledActivity) -> None:
    text = activity.title + activity.place_name
    if activity.kind == "station" and re.search(r"(?:镇|街道|县)$", activity.place_name):
        activity.kind = "other"
    if activity.kind == "other":
        if re.search(r"高铁|动车|列车|乘火车|乘飞机|航班", activity.title):
            activity.kind = "transport"
        elif re.search(r"酒店|宾馆|民宿|入住", text):
            activity.kind = "hotel"
        elif re.search(r"车站|[东南西北]站|机场|火车站", text):
            activity.kind = "station"
        elif re.search(r"午餐|晚餐|早餐|餐厅|小吃", text):
            activity.kind = "meal"
        elif re.search(r"游览|参观|景区|风景|公园|博物|寺庙|古镇|景点", text):
            activity.kind = "attraction"
    # 兼容旧路书：到站交通的时长是上一段路程，不是此站的停留时间。
    if activity.kind == "transport":
        activity.transport_mode = (
            "flight"
            if re.search(r"飞机|航班|机场", text)
            else "rail"
            if re.search(r"高铁|动车|铁路|列车|火车|川青", text)
            else "transfer"
        )
        activity.transport_minutes = (
            activity.transport_minutes
            if activity.transport_minutes is not None
            else activity.duration_minutes
        )
        activity.transport_cost = (
            activity.transport_cost if activity.transport_cost is not None else activity.cost
        )
        activity.transport_cost_basis = activity.cost_basis
        activity.transport_source_indices = list(activity.source_indices)
        activity.cost, activity.cost_source = None, "unknown"
        activity.duration_minutes = 20
        activity.kind = "station"
        activity.title = f"抵达{activity.place_name}" if activity.place_name else activity.title


def build_itinerary(drafts: list[DayDraft], requirements: Requirements) -> Itinerary:
    days = []
    seen = set()
    for draft in sorted(drafts, key=lambda d: d.day):
        if draft.day in seen or draft.day > (requirements.duration_days or 60):
            continue
        seen.add(draft.day)
        activities: list[ScheduledActivity] = []
        for item in draft.activities:
            activity = ScheduledActivity(id=uuid4().hex[:12], **item.model_dump())
            _kind(activity)
            if activities:
                last = activities[-1]
                same_place = bool(activity.place_name) and normal_name(
                    last.place_name
                ) == normal_name(activity.place_name)
                generic = normal_name(activity.title) == normal_name(activity.place_name)
                last_generic = normal_name(last.title) == normal_name(last.place_name)
                if (
                    same_place
                    and last.city == activity.city
                    and (last.title == activity.title or generic and last_generic)
                ):
                    last.notes = "；".join(
                        dict.fromkeys(filter(None, (last.notes, activity.notes)))
                    )[:2000]
                    last.source_indices = list(
                        dict.fromkeys(last.source_indices + activity.source_indices)
                    )[:20]
                    if last.cost is None:
                        last.cost, last.cost_basis, last.cost_source = (
                            activity.cost,
                            activity.cost_basis,
                            activity.cost_source,
                        )
                    continue
            activities.append(activity)
        days.append(
            DayPlan(
                day=draft.day,
                title=draft.title,
                travel_mode=draft.travel_mode or "auto",
                activities=activities,
            )
        )
    for number in range(1, (requirements.duration_days or max(seen, default=0)) + 1):
        if number not in seen:
            days.append(DayPlan(day=number, title="安排待补充"))
    return Itinerary(days=sorted(days, key=lambda day: day.day))


def schedule_day(day: DayPlan) -> None:
    """填充建议游玩时间；车次/航班的真实发车时间仍需按票面确认。"""
    day.schedule_notes = ["时间轴为建议安排；实际车次、航班及预约时间以票面为准。"]
    cursor = (
        _minutes(day.activities[0].start_time, day.activities[0].start_day_offset)
        if day.activities and day.activities[0].start_time
        else 9 * 60
    )
    after_origin = False
    for index, activity in enumerate(day.activities):
        if day.replan_context and not after_origin:
            if activity.id == day.replan_context.activity_id:
                cursor = _minutes(day.replan_context.time, day.replan_context.start_day_offset)
                after_origin = True
            continue  # 出发点及之前的记录不重新排时刻。
        if activity.booking:
            activity.start_time = activity.booking.start_time
            activity.start_day_offset = activity.booking.start_day_offset
            activity.duration_minutes = activity.booking.duration_minutes
            activity.time_is_suggested = False
        if index and index - 1 < len(day.legs):
            leg = day.legs[index - 1]
            if leg.duration_seconds is not None:
                cursor += math.ceil(leg.duration_seconds / 60)
        earliest_arrival = cursor
        if activity.start_time:
            requested = _minutes(activity.start_time, activity.start_day_offset)
            if requested < cursor and not activity.time_is_suggested:
                day.warnings.append(f"「{activity.title}」固定时间与前段路程冲突，可通过对话调整。")
            if not activity.time_is_suggested or activity.progress != "not_started":
                cursor = requested
            elif not after_origin:
                cursor = max(cursor, requested)
        activity.start_day_offset = cursor // 1440
        activity.start_time = f"{cursor % 1440 // 60:02d}:{cursor % 60:02d}"
        if activity.duration_minutes is None:
            activity.duration_minutes = {
                "station": 20,
                "hotel": 30,
                "meal": 60,
                "free_time": 60,
            }.get(activity.kind, 90)
        if after_origin or activity.progress == "not_started":
            cursor = max(cursor, earliest_arrival)
        cursor += activity.duration_minutes
    if cursor > 1440:
        day.schedule_notes.append("部分安排延续至次日，仍归在本行程日中，日期已在时刻旁标明。")
    if day.replan_context:
        departure_label = time_label(day.replan_context.time, day.replan_context.start_day_offset)
        day.schedule_notes.append(f"从当前地点 {departure_label} 出发，前面的活动记录保持原样。")


def summarise_day(day: DayPlan, requirements: Requirements) -> None:
    day.known_cost, day.estimated_cost = 0, 0
    day.unknown_cost_count = 0 if day.activities else 1
    people = requirements.travelers
    items: list[ScheduledActivity | RouteLeg] = [*day.activities, *day.legs]
    for item in items:
        basis = item.cost_basis
        if isinstance(item, RouteLeg) and basis == "unknown" and item.status == "ok":
            basis = "per_person" if item.mode == "transit" else "total"
        if item.cost is None or basis == "unknown" or basis == "per_person" and people is None:
            day.unknown_cost_count += 1
            continue
        amount = item.cost * (people if basis == "per_person" and people is not None else 1)
        day.known_cost += amount
        if item.cost_source in {"estimate", "manual"}:
            day.estimated_cost += amount
    day.known_cost, day.estimated_cost = round(day.known_cost, 2), round(day.estimated_cost, 2)
    timed = bool(day.activities) and all(
        leg.duration_seconds is not None and leg.status in {"ok", "planned"} for leg in day.legs
    )
    day.travel_minutes = (
        math.ceil(sum(leg.duration_seconds or 0 for leg in day.legs) / 60)
        if timed and day.checked_at
        else None
    )
    day.walking_meters = (
        sum(leg.walking_meters or 0 for leg in day.legs)
        if timed and day.checked_at and all(leg.walking_meters is not None for leg in day.legs)
        else None
    )
    if day.walking_meters is not None and day.walking_meters > 15000:
        day.warnings.append("当天地点间步行超过 15 公里，建议减少景点或调整交通方式。")
    if day.travel_minutes is not None and day.travel_minutes > 180:
        day.warnings.append("当天交通超过 3 小时，已在时间轴中预留路程。")
    for a, leg, b in zip(day.activities, day.legs, day.activities[1:], strict=False):
        if (
            a.start_time
            and b.start_time
            and a.duration_minutes
            and leg.duration_seconds is not None
            and _minutes(b.start_time, b.start_day_offset)
            - _minutes(a.start_time, a.start_day_offset)
            - a.duration_minutes
            < leg.duration_seconds / 60
        ):
            day.warnings.append(f"「{a.title}」到「{b.title}」预留交通时间不足。")
    if not day.activities:
        day.warnings.append("当天没有明确的活动安排，请在对话中补充。")


def _mode(day: DayPlan, origin: ScheduledActivity, destination: ScheduledActivity) -> TravelMode:
    if destination.transport_mode != "auto":
        return destination.transport_mode
    if re.search(r"乘.*?(高铁|动车|列车|火车)|川青铁路", destination.notes):
        return "rail"
    if day.travel_mode != "auto":
        return day.travel_mode
    if origin.place and destination.place:
        if (
            origin.place.citycode
            and destination.place.citycode
            and origin.place.citycode != destination.place.citycode
        ):
            return "transfer"
        if distance_meters(origin.place, destination.place) <= 1600:
            return "walking"
    return "transit"


def _planned_leg(
    origin: ScheduledActivity, destination: ScheduledActivity, mode: TravelMode
) -> RouteLeg:
    duration = destination.transport_minutes
    return RouteLeg(
        from_id=origin.id,
        to_id=destination.id,
        mode=mode,
        status="planned" if duration is not None else "unavailable",
        duration_seconds=duration * 60 if duration is not None else None,
        walking_meters=0 if mode in {"rail", "flight", "transfer"} else None,
        cost=destination.transport_cost,
        cost_basis=destination.transport_cost_basis,
        cost_source="reference"
        if destination.transport_source_indices
        else "estimate"
        if destination.transport_cost is not None
        else "unknown",
        source_indices=destination.transport_source_indices,
        message="按行程资料衔接，出发时间以实际票面/预约为准。"
        if mode in {"rail", "flight"}
        else "建议接驳安排，时间为规划估算。",
    )


async def _recommend_concrete_place(
    activity: ScheduledActivity, provider: MapProvider, city: str
) -> bool:
    """仅对区域泛称选一处真实餐厅/住宿建议，具体店名与分店不擅自替换。"""
    if activity.kind not in {"hotel", "meal"}:
        return False
    generic = re.search(
        r"(?:沟口住宿区|住宿区|住宿区域|酒店区域|镇中心餐馆|餐饮点|餐饮区|附近餐厅|周边餐厅|附近酒店)$",
        activity.place_name,
    )
    if not generic:
        return False
    area = activity.place_name[: generic.start()].strip()
    if len(area) < 2:
        return False
    category, prefix = ("酒店", "10") if activity.kind == "hotel" else ("餐厅", "05")
    candidates = await provider.search_places(f"{area} {category}", city)
    valid = [
        place
        for place in candidates
        if place.typecode.startswith(prefix)
        and area in place.name + place.address + place.district
        and (not city or city.removesuffix("市") in place.city + place.district)
    ]
    if not valid:
        return False
    activity.place = valid[0]
    activity.candidates = candidates
    activity.place_name = valid[0].name
    activity.place_status = "matched"
    activity.match_reason = f"根据{area}自动推荐的具体{category}，可通过对话替换。"
    activity.notes = (activity.notes + f"；{category}为建议选择，营业及预订情况请以商家为准。")[
        -2000:
    ]
    return True


async def check_day(
    day: DayPlan, requirements: Requirements, provider: MapProvider | None, *, copy_day: bool = True
) -> DayPlan:
    day = day.model_copy(deep=True) if copy_day else day
    old_legs = {(leg.from_id, leg.to_id): leg.model_copy(deep=True) for leg in day.legs}
    anchor = next(
        (
            i
            for i, a in enumerate(day.activities)
            if day.replan_context and a.id == day.replan_context.activity_id
        ),
        -1,
    )
    day.warnings, day.legs = [], []
    if provider:
        for index, activity in enumerate(day.activities):
            if index < anchor:
                continue
            if activity.place:
                continue
            if not activity.place_name or activity.kind == "free_time":
                activity.place_status = "not_found"
                continue
            try:
                candidates = await provider.search_places(
                    activity.place_name, activity.city or requirements.destination or ""
                )
                context = activity.model_copy(
                    update={"city": activity.city or requirements.destination or ""}
                )
                chosen, reason = choose_place(context, candidates)
                activity.candidates, activity.match_reason = candidates, reason
                activity.place = chosen
                if chosen and not activity.city:
                    activity.city = chosen.city
                activity.place_status = (
                    "matched" if chosen else "ambiguous" if candidates else "not_found"
                )
                if not chosen:
                    await _recommend_concrete_place(activity, provider, context.city)
            except (ProviderError, ValueError, KeyError, TypeError) as exc:
                activity.place_status = "unavailable"
                activity.match_reason = (
                    exc.message if isinstance(exc, ProviderError) else "地点服务暂未返回完整信息"
                )
        day.checked_at = datetime.now()
    else:
        day.checked_at = None
        day.warnings.append("地图暂未配置，建议行程仍可使用。")
    for index, (origin, destination) in enumerate(
        zip(day.activities, day.activities[1:], strict=False), 1
    ):
        mode = _mode(day, origin, destination)
        leg = _planned_leg(origin, destination, mode)
        if index <= anchor:
            day.legs.append(old_legs.get((origin.id, destination.id), leg))
            continue
        if (
            mode not in {"rail", "flight", "transfer"}
            and provider
            and origin.place
            and destination.place
        ):
            try:
                leg = await provider.route(origin.place, destination.place, mode)
                leg.from_id, leg.to_id = origin.id, destination.id
            except (ProviderError, ValueError, KeyError, TypeError) as exc:
                # 智能模式下公交无方案会自动尝试道路接驳，不要求用户逐项切换。
                if (
                    mode == "transit"
                    and day.travel_mode == "auto"
                    and destination.transport_mode == "auto"
                ):
                    try:
                        leg = await provider.route(origin.place, destination.place, "driving")
                        leg.from_id, leg.to_id = origin.id, destination.id
                        leg.message = "公交暂无可用方案，建议打车/驾车接驳。"
                    except (ProviderError, ValueError, KeyError, TypeError):
                        leg.message = (
                            exc.message if isinstance(exc, ProviderError) else "路线服务暂不可用"
                        )
                else:
                    leg.message = (
                        exc.message if isinstance(exc, ProviderError) else "路线服务暂不可用"
                    )
        elif mode not in {"rail", "flight", "transfer"} and leg.duration_seconds is None:
            leg.status = "unmatched" if provider else "unavailable"
            leg.message = "保留建议安排，地图恢复后可自动校验。"
        if leg.cost is None and destination.transport_cost is not None:
            leg.cost, leg.cost_basis = destination.transport_cost, destination.transport_cost_basis
            leg.cost_source = "reference" if destination.transport_source_indices else "estimate"
        day.legs.append(leg)
    if provider and any(a.place is None and a.place_name for a in day.activities):
        day.warnings.append("少数地点定位尚待确认，行程已保留；可直接告诉 AI 希望调整哪里。")
    if provider and any(leg.status in {"unavailable", "unmatched"} for leg in day.legs):
        day.warnings.append("部分交通使用建议安排，详细查询情况见对应连线。")
    schedule_day(day)
    summarise_day(day, requirements)
    return day


async def enrich_itinerary(
    itinerary: Itinerary,
    requirements: Requirements,
    settings: Settings,
    *,
    day_number: int | None = None,
) -> Itinerary:
    result = itinerary.model_copy(deep=True)
    semaphore = asyncio.Semaphore(2)
    timeout = httpx.Timeout(min(settings.amap_timeout_seconds, 8), connect=4)
    async with httpx.AsyncClient(timeout=timeout) as client:
        provider = AmapProvider(client, settings.amap_api_key) if settings.amap_api_key else None

        async def checked(day: DayPlan) -> DayPlan:
            if day_number is not None and day.day != day_number:
                return day
            async with semaphore:
                working = day.model_copy(deep=True)

                async def balanced(maps: MapProvider | None) -> DayPlan:
                    from app.services.replan_service import context_warnings, optional_to_remove

                    await check_day(working, requirements, maps, copy_day=False)
                    for _ in range(80):
                        remove = optional_to_remove(working)
                        if remove is None:
                            break
                        removed = working.activities.pop(remove)
                        assert working.replan_context is not None
                        working.replan_context.omitted_titles = list(
                            dict.fromkeys([*working.replan_context.omitted_titles, removed.title])
                        )[-40:]
                        await check_day(working, requirements, maps, copy_day=False)
                    working.warnings.extend(context_warnings(working))
                    return working

                try:
                    async with asyncio.timeout(35):
                        return await balanced(provider)
                except TimeoutError:
                    fallback = await balanced(None)
                    fallback.warnings.insert(
                        0, "地点服务响应较慢，已保留建议时间轴；稍后可重新校验。"
                    )
                    return fallback

        result.days = list(await asyncio.gather(*(checked(day) for day in result.days)))
    ids = {a.id for d in result.days for a in d.activities}
    for entry in result.expenses:
        if entry.activity_id not in ids:
            entry.activity_id = None
    result.map_status = (
        "unconfigured"
        if not provider
        else (
            "ready"
            if all(d.activities for d in result.days)
            and all(a.place or a.kind == "free_time" for d in result.days for a in d.activities)
            and all(leg.status in {"ok", "planned"} for d in result.days for leg in d.legs)
            else "partial"
        )
    )
    from app.services.budget_service import apply_budget_overrides

    apply_budget_overrides(result, requirements)
    from app.services.replan_service import check_overnight_links

    check_overnight_links(result)
    return result


def _text(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+.!|<>])", r"\\\1", " ".join(value.split()))


def render_days(itinerary: Itinerary) -> str:
    lines = ["## 四、每日详细行程", ""]
    for day in itinerary.days:
        lines.extend([f"### 第 {day.day} 天：{_text(day.title)}", ""])
        for index, activity in enumerate(day.activities, 1):
            time = (
                time_label(activity.start_time, activity.start_day_offset)
                if activity.start_time
                else "按当天安排"
            )
            duration = (
                f"，停留约 {activity.duration_minutes} 分钟" if activity.duration_minutes else ""
            )
            refs = "".join(f"[{i}]" for i in activity.source_indices)
            basis = {"per_person": "人均", "total": "全团", "unknown": "口径待核实"}[
                activity.cost_basis
            ]
            prefix = "估算 " if activity.cost_source in {"estimate", "manual"} else ""
            cost = (
                "费用未计入"
                if activity.cost is None
                else f"{prefix}{activity.cost:g} 元（{basis}）"
            )
            time_kind = "预约" if activity.booking else "建议"
            lines.append(
                f"{index}. **{time_kind} {time} · {_text(activity.title)}**"
                + ("（出发位置）" if activity.is_context_anchor else f"{duration}；{cost}。{refs}")
            )
            if activity.progress != "not_started":
                label = {"in_progress": "进行中", "completed": "已完成", "skipped": "已跳过"}[
                    activity.progress
                ]
                lines.append(f"   旅中状态：{label}")
            if activity.place_name:
                name = activity.place.name if activity.place else activity.place_name
                lines.append(f"   地点：{_text(name)}")
                if activity.place and activity.place.address:
                    place = activity.place
                    prefix = " ".join(
                        part
                        for part in (place.city, place.district)
                        if part and part not in place.address
                    )
                    lines.append(f"   地址：{_text((prefix + ' ' + place.address).strip())}")
            if activity.notes:
                lines.append(f"   {_text(activity.notes)}")
            if activity.booking:
                booked_label = time_label(
                    activity.booking.start_time, activity.booking.start_day_offset
                )
                lines.append(
                    "   已记录预约："
                    f"{booked_label}，"
                    f"停留 {activity.booking.duration_minutes} 分钟。"
                )
                if activity.booking.notes:
                    lines.append(f"   预约备注：{_text(activity.booking.notes)}")
            if index <= len(day.legs):
                leg = day.legs[index - 1]
                label = MODE_LABELS[leg.mode]
                if leg.duration_seconds is not None:
                    label += f"约 {math.ceil(leg.duration_seconds / 60)} 分钟"
                if leg.status != "ok":
                    label += "（建议安排）"
                lines.append(f"   → 下一站：{label}。{_text(leg.message)}")
        lines.append(
            f"\n活动与交通费用小计（全团）：{day.known_cost:g} 元，"
            f"其中估算 {day.estimated_cost:g} 元；未计入 {day.unknown_cost_count} 项。"
        )
        lines.extend(f"> {_text(note)}" for note in [*day.schedule_notes, *day.warnings])
        if day.linked_replan:
            linked = day.linked_replan
            lines.append(
                f"> 最近两天联动：第{linked.first_day}—{linked.second_day}天，"
                f"预留{linked.rest_minutes / 60:g}小时休息，"
                f"第{linked.second_day}天{linked.next_departure}出发。"
            )
            if linked.movement_notes:
                lines.append(
                    "> 跨天搬移："
                    + "；".join(_text(note) for note in linked.movement_notes)
                    + "。预算归到新行程日，实际付款日期与凭证保留。"
                )
        if day.replan_context and day.replan_context.omitted_titles:
            lines.append(
                "> 为满足时间约束，本次移出后续安排："
                + "、".join(_text(title) for title in day.replan_context.omitted_titles)
                + "。原安排保存在历史版本中。"
            )
        lines.append("")
    return "\n".join(lines)


def sync_markdown(markdown: str, itinerary: Itinerary, requirements: Requirements) -> str:
    from app.services.budget_service import apply_budget_overrides

    apply_budget_overrides(itinerary, requirements)
    daily = render_days(itinerary)
    pattern = r"^## (?:四[、.． ]*)?每日详细行程[^\n]*\n.*?(?=^## |\Z)"
    if re.search(pattern, markdown, flags=re.M | re.S):
        markdown = re.sub(pattern, lambda _: daily + "\n", markdown, flags=re.M | re.S)
    else:
        markdown += "\n\n" + daily
    total = sum(day.known_cost for day in itinerary.days)
    estimates = sum(day.estimated_cost for day in itinerary.days)
    unknown = sum(day.unknown_cost_count for day in itinerary.days)
    budget = requirements.budget
    if requirements.budget_basis == "per_person":
        budget = (
            budget * requirements.travelers
            if budget is not None and requirements.travelers
            else None
        )
    elif requirements.budget_basis is None:
        budget = None
    financial = (
        "## 五、费用预估\n\n"
        + f"活动及交通费用小计（全团）：**{total:g} 元**，其中估算 **{estimates:g} 元**；"
        f"另有 **{unknown}** 项未计入。\n\n" + "费用为规划用途，不代表完整旅行总价；"
        "住宿、往返大交通及未列出的餐饮等如未列入活动则另计。驾车油费、停车费未自动计入。\n\n"
        + (
            f"换算后的全团预算：{budget:g} 元。"
            if budget is not None
            else "预算人数或口径未明确，暂不判断是否超支。"
        )
        + ("当前小计已超过预算。" if budget is not None and total > budget else "")
        + "\n"
    )
    expense_pattern = r"^## 五[、.． ]*费用预估[^\n]*\n.*?(?=^## |\Z)"
    from app.services.budget_service import budget_overview

    overview = budget_overview(itinerary, requirements)
    financial += (
        f"\n统一费用清单预估小计：{overview.planned_cents / 100:.2f} 元；"
        f"按已结算实付与未结算预估推算：{overview.projected_cents / 100:.2f} 元。"
        f"仍有 {overview.unknown_count} 项费用未定，不代表最终总价。\n\n"
        "| 项目 | 预估（全团） | 已记净消费 | 状态 |\n| --- | ---: | ---: | --- |\n"
    )
    for row in overview.rows:
        planned = f"{row.planned_cents / 100:.2f}" if row.planned_cents is not None else "待核实"
        financial += (
            f"| {_text(row.title).replace('|', '／')} | {planned} | "
            f"{row.actual_cents / 100:.2f} | {'已结算' if row.settled else '待结算'} |\n"
        )
    if re.search(expense_pattern, markdown, flags=re.M | re.S):
        markdown = re.sub(expense_pattern, lambda _: financial + "\n", markdown, flags=re.M | re.S)
    else:
        markdown += "\n" + financial
    progress_count = sum(a.progress == "completed" for d in itinerary.days for a in d.activities)
    actual_cents = sum(e.amount_cents for e in itinerary.expenses)
    ledger = itinerary.ledger_summary
    travel = (
        "## 旅中记录与实际消费\n\n"
        f"已完成 {progress_count} 个停留点；"
        f"实际消费合计：**{actual_cents / 100:.2f} 元**（全团）。\n\n"
        "实际消费独立记账，不与上方规划估算相加；余额按已记消费计算，未记支出不在其中。\n\n"
    )
    if itinerary.adjustments:
        travel += (
            f"已到账退款：**{ledger.refunded_cents / 100:.2f} 元**；"
            f"已到账报销：**{ledger.reimbursed_cents / 100:.2f} 元**。\n\n"
            f"净消费（实付减已到账退款）：**{ledger.net_expense_cents / 100:.2f} 元**；"
            f"净垫付（再减已到账报销）：**{ledger.net_outlay_cents / 100:.2f} 元**（全团）。\n\n"
            f"待到账退款：{ledger.pending_refund_cents / 100:.2f} 元；"
            f"待到账报销：{ledger.pending_reimbursement_cents / 100:.2f} 元。"
            "待到账款项不冲减实际金额。\n\n"
            "报销作为资金回收单列，不重复冲减旅行消费；预算余量按净消费计算。\n\n"
        )
    if budget is not None:
        travel += f"预算减已记净消费：{budget - ledger.net_expense_cents / 100:.2f} 元。\n\n"
    if ledger.over_recovered_cents:
        travel += (
            f"> 部分消费的到账回款超过原支出，超出 {ledger.over_recovered_cents / 100:.2f} 元，"
            "请核对回款记录。\n\n"
        )
    for entry in itinerary.expenses:
        from app.services.expense_dates import planned_expense_day

        day_label = f"第 {entry.day} 天" if entry.day else "未关联日期"
        travel += (
            f"- 付款日：{day_label} · {_text(entry.title)}：{entry.amount_cents / 100:.2f} 元\n"
        )
        planned_day = planned_expense_day(itinerary, entry)
        if planned_day is not None and planned_day != entry.day:
            travel += f"  关联行程：第 {planned_day} 天；付款日期保持原记录。\n"
        if entry.notes:
            travel += f"  {_text(entry.notes)}\n"
        for adjustment in itinerary.adjustments:
            if adjustment.expense_id == entry.id:
                kind = "退款" if adjustment.kind == "refund" else "报销"
                status = "已到账" if adjustment.status == "received" else "待到账"
                travel += f"  - {kind}（{status}）：{adjustment.amount_cents / 100:.2f} 元。\n"
                if adjustment.notes:
                    travel += f"    {_text(adjustment.notes)}\n"
    travel_pattern = r"^## 旅中记录与实际消费[^\n]*\n.*?(?=^## |\Z)"
    if re.search(travel_pattern, markdown, flags=re.M | re.S):
        markdown = re.sub(travel_pattern, lambda _: travel + "\n", markdown, flags=re.M | re.S)
    elif itinerary.expenses or any(
        a.progress != "not_started" for d in itinerary.days for a in d.activities
    ):
        markdown += "\n\n" + travel
    return markdown
