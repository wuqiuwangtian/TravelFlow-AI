"""从需求收集开始的完整旅程替身；不预填正式计划或旅中记录。"""

import hashlib

from place_fixtures import candidates

from app.domain.itinerary import Activity, DayDraft, Place, RouteLeg

FLOW_MARKER = "连贯主流程验收"


def journey_drafts():
    def activity(title, place, time, duration, kind="attraction", cost=None, offset=0):
        return Activity(
            title=title,
            place_name=place,
            city="北京",
            kind=kind,
            start_time=time,
            start_day_offset=offset,
            duration_minutes=duration,
            cost=cost,
            cost_basis="total" if cost is not None else "unknown",
            cost_source="estimate" if cost is not None else "unknown",
            transport_minutes=30,
        )

    departure = activity("上海出发", "上海虹桥站", "05:00", 20, kind="station", cost=0)
    departure.city = "上海"
    first_visit = activity("故宫游览", "故宫", "10:30", 90, cost=60)
    first_visit.transport_mode, first_visit.transport_minutes = "rail", 300
    return [
        DayDraft(
            day=1,
            title="城市漫步与夜间入住",
            activities=[
                departure,
                first_visit,
                activity("午餐", "示例餐厅", "12:00", 60, kind="meal"),
                activity("车站出发", "北京站", "22:00", 20, kind="station", cost=0),
                activity("夜间散步", "东街", "23:00", 60, cost=10),
                activity("入住示例酒店", "示例酒店", "00:30", 30, kind="hotel", cost=300, offset=1),
            ],
        ),
        DayDraft(
            day=2,
            title="公园与展览",
            activities=[
                activity("公园漫步", "示例公园", "08:00", 60, cost=20),
                activity("展览参观", "示例展览馆", "14:00", 60),
                activity("返回示例酒店", "示例酒店", "17:00", 30, kind="hotel", cost=300),
            ],
        ),
        DayDraft(
            day=3,
            title="第三天保持独立",
            activities=[
                activity("第三天公园", "第三天公园", "09:00", 60),
            ],
        ),
    ]


class JourneyMaps:
    """提供固定候选和路段，真实匹配、排时与费用计算仍由业务代码执行。"""

    async def search_places(self, keyword, city):
        if keyword == "示例餐厅":
            return candidates()
        prefix = "10" if "酒店" in keyword else "15" if "站" in keyword else "11"
        return [
            Place(
                id=hashlib.sha256(keyword.encode()).hexdigest()[:12],
                name=keyword,
                city=city,
                district="东城区",
                address="隔离样本路1号",
                typecode=prefix + "0100",
                longitude=116.4,
                latitude=39.9,
            )
        ]

    async def route(self, origin, destination, mode):
        return RouteLeg(
            from_id=origin.id,
            to_id=destination.id,
            mode=mode,
            status="ok",
            distance_meters=1200,
            duration_seconds=1800,
            walking_meters=200,
            cost=None,
            message="隔离路段耗时已返回，费用保持未知。",
        )
