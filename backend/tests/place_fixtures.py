"""测试专用地点与行程，坐标仅用于验证路由和界面，不代表商户资料。"""

from app.domain.itinerary import (
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
