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


def _minutes(value: str) -> int:
    h, m = map(int, value.split(":"))
    return h * 60 + m


def schedule_day(day: DayPlan) -> None:
    """填充建议游玩时间；车次/航班的真实发车时间仍需按票面确认。"""
    day.schedule_notes = ["时间轴为建议安排；实际车次、航班及预约时间以票面为准。"]
    cursor = (
        _minutes(day.activities[0].start_time)
        if day.activities and day.activities[0].start_time
        else 9 * 60
    )
    for index, activity in enumerate(day.activities):
        if index and index - 1 < len(day.legs):
            leg = day.legs[index - 1]
            if leg.duration_seconds is not None:
                cursor += math.ceil(leg.duration_seconds / 60)
        if activity.start_time:
            requested = _minutes(activity.start_time)
            if requested < cursor and not activity.time_is_suggested:
                day.warnings.append(f"「{activity.title}」固定时间与前段路程冲突，可通过对话调整。")
            cursor = requested if not activity.time_is_suggested else max(cursor, requested)
        if cursor >= 24 * 60:
            day.warnings.append("当天安排将跨过午夜，建议减少活动或增加一天。")
        activity.start_time = f"{min(cursor, 1439) // 60:02d}:{min(cursor, 1439) % 60:02d}"
        if activity.duration_minutes is None:
            activity.duration_minutes = {
                "station": 20,
                "hotel": 30,
                "meal": 60,
                "free_time": 60,
            }.get(activity.kind, 90)
        cursor += activity.duration_minutes


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
            and _minutes(b.start_time) - _minutes(a.start_time) - a.duration_minutes
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
    day.warnings, day.legs = [], []
    if provider:
        for activity in day.activities:
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
    for origin, destination in zip(day.activities, day.activities[1:], strict=False):
        mode = _mode(day, origin, destination)
        leg = _planned_leg(origin, destination, mode)
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
                try:
                    async with asyncio.timeout(35):
                        return await check_day(working, requirements, provider, copy_day=False)
                except TimeoutError:
                    fallback = await check_day(working, requirements, None)
                    fallback.warnings = ["地点服务响应较慢，已保留建议时间轴；稍后可重新校验。"]
                    return fallback

        result.days = list(await asyncio.gather(*(checked(day) for day in result.days)))
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
    return result


def _text(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+.!|<>])", r"\\\1", " ".join(value.split()))


def render_days(itinerary: Itinerary) -> str:
    lines = ["## 四、每日详细行程", ""]
    for day in itinerary.days:
        lines.extend([f"### 第 {day.day} 天：{_text(day.title)}", ""])
        for index, activity in enumerate(day.activities, 1):
            time = activity.start_time or "按当天安排"
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
            lines.append(
                f"{index}. **建议 {time} · {_text(activity.title)}**{duration}；{cost}。{refs}"
            )
            if activity.place_name:
                name = activity.place.name if activity.place else activity.place_name
                lines.append(f"   地点：{_text(name)}")
            if activity.notes:
                lines.append(f"   {_text(activity.notes)}")
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
        lines.append("")
    return "\n".join(lines)


def sync_markdown(markdown: str, itinerary: Itinerary, requirements: Requirements) -> str:
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
    if re.search(expense_pattern, markdown, flags=re.M | re.S):
        markdown = re.sub(expense_pattern, lambda _: financial + "\n", markdown, flags=re.M | re.S)
    else:
        markdown += "\n" + financial
    return markdown
