"""可编辑行程及地图结果。坐标、路线由地图服务填充，不交给模型编造。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

TravelMode = Literal["auto", "walking", "transit", "driving", "rail", "flight", "transfer"]
CostBasis = Literal["per_person", "total", "unknown"]
CostSource = Literal["reference", "estimate", "manual", "unknown"]
ActivityProgress = Literal["not_started", "in_progress", "completed", "skipped"]
ExpenseCategory = Literal["transport", "lodging", "meal", "ticket", "shopping", "other"]


class Place(BaseModel):
    id: str
    name: str
    address: str = ""
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    citycode: str = ""
    city: str = ""
    district: str = ""
    typecode: str = ""

    @property
    def location(self) -> str:
        return f"{self.longitude:.6f},{self.latitude:.6f}"


class Activity(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    place_name: str = Field(default="", max_length=100)
    city: str = Field(default="", max_length=50)
    kind: Literal["attraction", "station", "hotel", "meal", "free_time", "transport", "other"] = (
        "other"
    )
    address_hint: str = Field(default="", max_length=200)
    start_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost_basis: Literal["per_person", "total", "unknown"] = "unknown"
    cost_source: CostSource = "unknown"
    # 表示到达此站的交通，交通耗时与费用不再混进游玩活动中。
    transport_mode: TravelMode = "auto"
    transport_minutes: int | None = Field(default=None, ge=0, le=2880)
    transport_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    transport_cost_basis: CostBasis = "unknown"
    transport_source_indices: list[int] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=2000)
    source_indices: list[int] = Field(default_factory=list, max_length=20)


class ScheduledActivity(Activity):
    id: str
    place: Place | None = None
    place_status: Literal["pending", "matched", "ambiguous", "not_found", "unavailable"] = "pending"
    candidates: list[Place] = Field(default_factory=list)
    match_reason: str = ""
    time_is_suggested: bool = True
    progress: ActivityProgress = "not_started"
    progress_updated_at: datetime | None = None


class ExpenseInput(BaseModel):
    title: str = Field(min_length=1, max_length=100, pattern=r"\S")
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2, allow_inf_nan=False)
    category: ExpenseCategory = "other"
    day: int | None = Field(default=None, ge=1, le=60)
    activity_id: str | None = Field(default=None, min_length=1, max_length=100)
    notes: str = Field(default="", max_length=1000)


class ExpenseCreate(ExpenseInput):
    request_id: str = Field(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")


class Expense(BaseModel):
    id: str
    title: str
    amount_cents: int = Field(gt=0)
    category: ExpenseCategory = "other"
    day: int | None = None
    activity_id: str | None = None
    notes: str = ""
    created_at: datetime
    updated_at: datetime


class DayDraft(BaseModel):
    day: int = Field(ge=1, le=60)
    title: str = Field(default="", max_length=100)
    travel_mode: TravelMode | None = Field(
        default=None, description="当天明确统一采用的交通方式，未说明则留空"
    )
    activities: list[Activity] = Field(default_factory=list, max_length=20)


class RouteLeg(BaseModel):
    from_id: str
    to_id: str
    mode: TravelMode
    status: Literal["ok", "planned", "unavailable", "unmatched"] = "unavailable"
    distance_meters: float | None = None
    duration_seconds: float | None = None
    walking_meters: float | None = None
    cost: float | None = None
    polyline: list[str] = Field(default_factory=list)
    message: str = ""
    cost_basis: CostBasis = "unknown"
    cost_source: CostSource = "unknown"
    source_indices: list[int] = Field(default_factory=list)


class DayPlan(BaseModel):
    day: int = Field(ge=1, le=60)
    title: str = ""
    activities: list[ScheduledActivity] = Field(default_factory=list, max_length=20)
    travel_mode: TravelMode = "auto"
    legs: list[RouteLeg] = Field(default_factory=list)
    checked_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)
    travel_minutes: int | None = None
    walking_meters: float | None = None
    known_cost: float = 0
    unknown_cost_count: int = 0
    estimated_cost: float = 0
    schedule_notes: list[str] = Field(default_factory=list)


class Itinerary(BaseModel):
    days: list[DayPlan] = Field(default_factory=list, max_length=60)
    map_status: Literal["unconfigured", "ready", "partial"] = "unconfigured"
    expenses: list[Expense] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def unique_days(self) -> Itinerary:
        numbers = [day.day for day in self.days]
        if len(numbers) != len(set(numbers)):
            raise ValueError("行程天数不能重复")
        return self


class ActivityUpdate(BaseModel):
    """完整替换一个活动；不接受客户端提交路线或计算后的费用。"""

    activity: Activity
    place_id: str | None = Field(default=None, max_length=100)
