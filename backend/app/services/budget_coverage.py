"""只读核对费用清单覆盖范围，不补造活动、金额或已确认事实。"""

import re

from app.domain.itinerary import BudgetRow, Itinerary
from app.domain.models import Requirements


def _city(value: str | None) -> str:
    return (value or "").strip().removesuffix("市")


def coverage_warnings(
    itinerary: Itinerary, requirements: Requirements, rows: list[BudgetRow]
) -> list[str]:
    days = sorted(itinerary.days, key=lambda day: day.day)
    if not any(day.activities for day in days):
        return []
    warnings = []
    nights = range(1, requirements.duration_days or max(day.day for day in days))
    lodging = [row for row in rows if row.category == "lodging"]
    missing_nights = [
        night for night in nights if not any(row.day in (None, night) for row in lodging)
    ]
    if missing_nights:
        labels = "、".join(str(night) for night in missing_nights)
        warnings.append(f"第{labels}晚住宿费用尚未列入清单，请确认住宿方式及是否需要支出。")

    for day in days:
        if not day.activities:
            continue
        first = day.activities[0]
        if (
            not first.is_context_anchor
            and first.progress != "skipped"
            and (
                (first.transport_cost or 0) > 0
                or first.transport_cost is None
                and (first.transport_minutes or 0) > 0
            )
        ):
            warnings.append(
                f"第{day.day}天首站前的交通信息没有对应起点和路段，未作为独立路段计费；"
                "请核对交通起终点，以及是否已在其他费用项目中计入。"
            )

    origin, destination = _city(requirements.origin), _city(requirements.destination)
    if not origin or not destination or origin == destination:
        return warnings
    outbound = inbound = False
    for row in rows:
        if row.category != "transport" or row.source != "custom":
            continue
        # 只有明确的整趟／单程项目才补足对应范围，市内打车不能代表往返大交通。
        if re.search(r"不含|不包括|另计|未计", row.title):
            continue
        outbound |= bool(re.search(r"往返|去程", row.title))
        inbound |= bool(re.search(r"往返|返程|回程", row.title))
    for day in days:
        activities = {activity.id: activity for activity in day.activities}
        for leg in day.legs:
            start, end = activities.get(leg.from_id), activities.get(leg.to_id)
            if not start or not end or start.is_context_anchor or end.progress == "skipped":
                continue
            start_city = _city(start.city or (start.place.city if start.place else None))
            end_city = _city(end.city or (end.place.city if end.place else None))
            if start_city and end_city and start_city != end_city:
                outbound |= start_city == origin
                inbound |= end_city == origin
    if not outbound:
        warnings.append(
            "尚未识别到去程交通费用安排，请确认是否需要及是否已计入；市内交通不代表去程大交通。"
        )
    if not inbound:
        warnings.append(
            "尚未识别到返程交通费用安排，请确认是否需要及是否已计入；不会自动假定必须原路返回。"
        )
    return warnings
