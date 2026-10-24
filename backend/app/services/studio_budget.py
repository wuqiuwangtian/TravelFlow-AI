"""明确口径的分类预留：只补未列分类，不覆盖未知分项或人工预算。"""

from app.domain.itinerary import BudgetItem, ExpenseCategory, Itinerary
from app.domain.models import Requirements
from app.domain.studio_data import BudgetRules, BudgetSupplement
from app.services.budget_service import budget_overview


def supplements(
    itinerary: Itinerary, requirements: Requirements, rules: BudgetRules
) -> tuple[list[BudgetSupplement], list[str]]:
    view = budget_overview(itinerary, requirements)
    result: list[BudgetSupplement] = []
    warnings: list[str] = []
    existing = {row.id for row in view.rows}

    def add(
        id: str,
        title: str,
        category: ExpenseCategory,
        day: int | None,
        rate: int,
        units: int | None,
        explanation: str,
    ) -> None:
        if id in existing:
            warnings.append(f"「{title}」已有预算，沿用现有金额；如需调整请在正式预算中编辑。")
            return
        amount = rate * units if units is not None else 0 if rate == 0 else None
        if amount is None:
            warnings.append(
                f"「{title}」缺少{'房间数' if category == 'lodging' else '人数'}，金额保留待核实。"
            )
        result.append(
            BudgetSupplement(
                item=BudgetItem(
                    id=id, title=title, category=category, day=day, planned_cents=amount
                ),
                explanation=explanation,
            )
        )

    def listed(category: ExpenseCategory, day: int) -> bool:
        rows = [r for r in view.rows if r.category == category and r.day in {None, day}]
        if rows:
            label = {"meal": "饮食", "lodging": "住宿"}[category]
            warnings.append(
                f"第{day}天{label}已有项目"
                "（含待核实或未指定日期的项目），不另加整日预留，避免重复。"
            )
        return bool(rows)

    days = sorted({day.day for day in itinerary.days})
    for day in days:
        if rules.meal_per_person_day_cents is not None and not listed("meal", day):
            add(
                f"studio-meal-day-{day}",
                f"第{day}天饮食预留（按设置估算）",
                "meal",
                day,
                rules.meal_per_person_day_cents,
                requirements.travelers,
                f"¥{rules.meal_per_person_day_cents / 100:.2f}/人/天 × "
                f"{requirements.travelers or '待定'}人；仅补未列饮食的日期。",
            )
        if (
            rules.lodging_per_room_night_cents is not None
            and day != days[-1]
            and not listed("lodging", day)
        ):
            add(
                f"studio-lodging-day-{day}",
                f"第{day}晚住宿预留（按设置估算）",
                "lodging",
                day,
                rules.lodging_per_room_night_cents,
                rules.lodging_rooms,
                f"¥{rules.lodging_per_room_night_cents / 100:.2f}/间/晚 × "
                f"{rules.lodging_rooms or '待定'}间；按每日一晚、末日不住宿计算。",
            )
    if rules.lodging_per_room_night_cents is not None:
        warnings.append(
            "住宿按行程天数减一晚预留，不核实酒店、入住日期或夜间交通；特殊过夜安排请在正式预算中调整。"
        )
    if rules.round_trip_per_person_cents is not None:
        has_intercity = any(
            a.kind in {"station", "transport"} or a.transport_mode in {"rail", "flight", "transfer"}
            for day in itinerary.days
            for a in day.activities
            if not a.is_context_anchor and a.progress != "skipped"
        ) or any(
            leg.mode in {"rail", "flight", "transfer"} for day in itinerary.days for leg in day.legs
        )
        if has_intercity or any(
            r.category == "transport" and r.source == "custom" for r in view.rows
        ):
            warnings.append(
                "已有车站、交通活动、大交通安排或自定义交通预算，不另加整趟往返预留；缺少的单程请单独补充。"
            )
        else:
            add(
                "studio-round-trip",
                "往返大交通预留（按设置估算）",
                "transport",
                None,
                rules.round_trip_per_person_cents,
                requirements.travelers,
                f"¥{rules.round_trip_per_person_cents / 100:.2f}/人/往返 × "
                f"{requirements.travelers or '待定'}人；不是车票报价，不生成订单。",
            )
    if rules.contingency_cents is not None:
        add(
            "studio-contingency",
            "全团备用金（按设置预留）",
            "other",
            None,
            rules.contingency_cents,
            1,
            "全团整趟固定预留，不代表已经消费。",
        )
    return result, list(dict.fromkeys(warnings))
