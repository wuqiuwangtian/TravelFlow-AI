"""测试专用地点与行程，坐标仅用于验证路由和界面，不代表商户资料。"""

from app.domain.itinerary import (
    Booking,
    BudgetItem,
    DayPlan,
    ExpenseCreate,
    Itinerary,
    Place,
    ScheduledActivity,
)
from app.domain.models import Requirements
from app.services.budget_service import apply_budget_overrides
from app.services.travel_service import add_expense


def candidates():
    return [
        Place(
            id="east",
            name="示例餐厅(望京店)",
            city="北京",
            district="朝阳区",
            address="望京路1号",
            longitude=116.48,
            latitude=39.99,
            typecode="050100",
        ),
        Place(
            id="west",
            name="示例餐厅(西单店)",
            city="北京",
            district="西城区",
            address="西单路2号",
            longitude=116.37,
            latitude=39.91,
            typecode="050100",
        ),
    ]


def place_itinerary():
    places = candidates()
    hotel = Place(
        id="hotel",
        name="示例酒店",
        city="北京",
        district="东城区",
        address="东街9号",
        longitude=116.4,
        latitude=39.92,
        typecode="100100",
    )
    itinerary = Itinerary(
        days=[
            DayPlan(
                day=1,
                title="第一天",
                activities=[
                    ScheduledActivity(
                        id="lunch",
                        title="午餐",
                        place_name="示例餐厅",
                        city="北京",
                        kind="meal",
                        candidates=places,
                        place_status="ambiguous",
                        cost=88,
                        cost_basis="total",
                        cost_source="manual",
                    ),
                    ScheduledActivity(
                        id="hotel",
                        title="入住酒店",
                        place_name=hotel.name,
                        city="北京",
                        kind="hotel",
                        place=hotel,
                        place_status="matched",
                        cost=300,
                        cost_basis="total",
                        cost_source="estimate",
                    ),
                ],
            ),
            DayPlan(
                day=2,
                title="第二天",
                activities=[
                    ScheduledActivity(
                        id="dinner",
                        title="晚餐",
                        place_name="示例餐厅",
                        city="北京",
                        kind="meal",
                        candidates=places,
                        place_status="ambiguous",
                    ),
                ],
            ),
        ]
    )
    itinerary.budget_items.append(
        BudgetItem(
            id="stay-lunch",
            title="午餐",
            category="meal",
            day=1,
            activity_id="lunch",
            source="activity",
            planned_cents=8800,
        )
    )
    add_expense(
        itinerary,
        ExpenseCreate(
            title="午餐押金",
            amount="20",
            day=1,
            category="meal",
            activity_id="lunch",
            budget_item_id="stay-lunch",
            request_id="fixture-lunch-deposit",
        ),
    )
    apply_budget_overrides(itinerary, Requirements())
    return itinerary


def connected_place_itinerary(*, matched=True):
    plan = place_itinerary()
    lunch, hotel = plan.days[0].activities
    lunch.place = candidates()[0] if matched else None
    lunch.place_status = "matched" if matched else "ambiguous"
    lunch.duration_minutes = 60
    lunch.transport_mode, lunch.transport_minutes = "transit", 5
    lunch.transport_cost, lunch.transport_cost_basis = 6, "total"
    lunch.transport_source_indices = [1]
    hotel.booking = Booking(start_time="12:00", duration_minutes=60)
    hotel.transport_mode, hotel.transport_minutes = "transit", 10
    hotel.transport_cost, hotel.transport_cost_basis = 8, "total"
    hotel.transport_source_indices = [2]
    plan.days[0].activities.insert(
        0,
        ScheduledActivity(
            id="park",
            title="上午公园",
            city="北京",
            place=hotel.place,
            place_status="matched",
            start_time="09:00",
            duration_minutes=30,
        ),
    )
    plan.days[0].activities.append(
        ScheduledActivity(
            id="last",
            title="下午散步",
            city="北京",
            place=hotel.place,
            place_status="matched",
            transport_minutes=15,
            transport_cost=9,
            transport_cost_basis="total",
            transport_source_indices=[3],
        )
    )
    return plan


def replan_itinerary():
    plan = place_itinerary()
    hotel = plan.days[0].activities[1].place
    origin = ScheduledActivity(
        id="departure",
        title="规划酒店出发点",
        place_name="示例酒店",
        city="北京",
        place=hotel,
        place_status="matched",
        start_time="09:00",
        duration_minutes=0,
        cost=0,
        cost_basis="total",
        is_context_anchor=True,
        is_planning_origin=True,
    )
    booked = plan.days[1].activities[0]
    booked.title, booked.place_name, booked.kind = "预约展馆", "示例展馆", "attraction"
    booked.place = candidates()[0].model_copy(update={"id": "museum", "name": "示例展馆"})
    booked.place_status = "matched"
    booked.booking = Booking(start_time="10:00", duration_minutes=120)
    booked.transport_mode, booked.transport_minutes = "transit", 45
    booked.transport_cost, booked.transport_cost_basis = 8, "total"
    plan.days[1].activities.insert(0, origin)
    return plan


def logistics_itinerary():
    plan = replan_itinerary()
    plan.days[1].activities.extend(
        [
            ScheduledActivity(
                id="optional-park",
                title="可选公园",
                place_name="公园",
                city="北京",
                duration_minutes=90,
                transport_minutes=10,
            ),
            ScheduledActivity(
                id="departure-station",
                title="返程去北京南站",
                place_name="北京南站",
                city="北京",
                kind="station",
                duration_minutes=20,
                transport_minutes=30,
                transport_cost=12,
                transport_cost_basis="total",
            ),
            ScheduledActivity(
                id="return-train",
                title="高铁返上海",
                place_name="上海虹桥站",
                city="上海",
                kind="station",
                duration_minutes=20,
                transport_mode="rail",
                transport_minutes=120,
            ),
            ScheduledActivity(
                id="overnight-hotel",
                title="过夜住宿",
                place_name="示例中转酒店",
                city="上海",
                kind="hotel",
                duration_minutes=30,
                transport_minutes=15,
                cost=300,
                cost_basis="total",
            ),
        ]
    )
    return plan
