"""工程内的结构化数据，不包含正式旅程的消费、预约或个人附件。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.itinerary import BudgetItem, BudgetOverview, DayPlan
from app.domain.models import Requirements, TodoItem
from app.domain.studio_candidates import AllocationReport


class SelectionDecision(BaseModel):
    day: int
    activity_id: str
    title: str
    action: Literal["removed", "kept", "duration"]
    reason: str


class SelectionReport(BaseModel):
    node_title: str
    before_count: int
    after_count: int
    decisions: list[SelectionDecision] = Field(default_factory=list)


class RouteOrderReport(BaseModel):
    day: int
    before: list[str]
    after: list[str]
    before_meters: float | None = None
    after_meters: float | None = None
    before_travel_minutes: int | None = None
    after_travel_minutes: int | None = None
    changed: bool = False
    reason: str


class StudioPlanData(BaseModel):
    requirements: Requirements
    days: list[DayPlan] = Field(max_length=60)
    todos: list[TodoItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    route_status: Literal["unchecked", "unconfigured", "partial", "ready"] = "unchecked"
    selection: SelectionReport | None = None
    optimization: list[RouteOrderReport] = Field(default_factory=list)
    allocation: AllocationReport | None = None


class BudgetRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meal_per_person_day_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    lodging_per_room_night_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    lodging_rooms: int | None = Field(default=None, ge=1, le=1000)
    round_trip_per_person_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    contingency_cents: int | None = Field(default=None, ge=0, le=100_000_000)


class BudgetSupplement(BaseModel):
    item: BudgetItem
    explanation: str


class StudioBudgetData(BaseModel):
    # 绑定精确输入，禁止把另一套路线的预算混到当前成稿。
    plan_key: str
    overview: BudgetOverview
    categories: dict[str, int]
    warnings: list[str] = Field(default_factory=list)
    rules: BudgetRules = Field(default_factory=BudgetRules)
    supplements: list[BudgetSupplement] = Field(default_factory=list)
