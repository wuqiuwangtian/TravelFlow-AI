"""工程内的结构化数据，不包含正式旅程的消费、预约或个人附件。"""

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.itinerary import BudgetOverview, DayPlan
from app.domain.models import Requirements, TodoItem


class StudioPlanData(BaseModel):
    requirements: Requirements
    days: list[DayPlan] = Field(max_length=60)
    todos: list[TodoItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    route_status: Literal["unchecked", "unconfigured", "partial", "ready"] = "unchecked"


class StudioBudgetData(BaseModel):
    # 绑定精确输入，禁止把另一套路线的预算混到当前成稿。
    plan_key: str
    overview: BudgetOverview
    categories: dict[str, int]
    warnings: list[str] = Field(default_factory=list)
