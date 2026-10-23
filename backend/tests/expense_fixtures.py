"""同日同名消费的隔离验收数据，不含真实账本或凭证。"""

from place_fixtures import place_itinerary

from app.domain.itinerary import ExpenseCreate
from app.services.travel_service import add_expense


def expense_itinerary():
    itinerary = place_itinerary()
    itinerary.expenses.clear()
    for index, note in enumerate(("上午在餐厅", "下午在酒店"), 1):
        add_expense(
            itinerary,
            ExpenseCreate(
                title="咖啡",
                amount="20",
                category="meal",
                day=1,
                activity_id="lunch" if index == 1 else "hotel",
                budget_item_id="stay-lunch",
                settles_budget=index == 2,
                notes=note,
                request_id=f"fixture-coffee-{index}",
            ),
        )
    return itinerary
