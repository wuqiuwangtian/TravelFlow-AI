"""候选池独立于每日计划；坐标仅来自地图节点，不包含预约或旅中事实。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.itinerary import Place
from app.domain.models import Requirements


class CandidateSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    place_name: str = Field(min_length=1, max_length=100)
    city: str = Field(default="", max_length=50)
    reason: str = Field(default="", max_length=600)
    duration_minutes: int | None = Field(default=None, ge=15, le=480)
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost_basis: Literal["per_person", "total", "unknown"] = "unknown"
    source_indices: list[int] = Field(default_factory=list, max_length=20)


class CandidateDraft(BaseModel):
    ready: bool
    reason: str = Field(default="", max_length=1000)
    requirements: Requirements
    places: list[CandidateSuggestion] = Field(default_factory=list, max_length=40)


class CandidateLocation(BaseModel):
    status: Literal["matched", "ambiguous", "not_found", "unavailable", "unconfigured"]
    place: Place | None = None
    candidates: list[Place] = Field(default_factory=list, max_length=8)
    reason: str
    checked_at: datetime | None = None
    selected_id: str | None = None


class PoolLocationReport(BaseModel):
    status: Literal["ready", "partial", "unconfigured"]
    checked_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)


class CandidatePlace(CandidateSuggestion):
    id: str
    cost_source: Literal["reference", "estimate", "unknown"] = "unknown"
    location: CandidateLocation | None = None


class CandidatePool(BaseModel):
    requirements: Requirements
    places: list[CandidatePlace] = Field(min_length=1, max_length=40)
    warnings: list[str] = Field(default_factory=list)
    location: PoolLocationReport | None = None


class AllocationDecision(BaseModel):
    candidate_id: str
    place_name: str
    day: int | None = None
    reason: str


class GeographicGroup(BaseModel):
    day: int
    places: list[str]
    districts: list[str]


class GeographyReport(BaseModel):
    applied: bool = False
    reason: str
    before_spread_meters: float | None = None
    after_spread_meters: float | None = None
    groups: list[GeographicGroup] = Field(default_factory=list)


class AllocationReport(BaseModel):
    pool_count: int
    selected_count: int
    pace: Literal["relaxed", "balanced", "full"]
    max_per_day: int
    minutes_per_day: int
    buffer_minutes: int
    decisions: list[AllocationDecision] = Field(default_factory=list)
    geography: GeographyReport | None = None
    locations_checked_at: datetime | None = None
