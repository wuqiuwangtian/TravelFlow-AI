"""候选池独立于每日计划，不携带坐标、预约或旅中事实。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


class CandidatePlace(CandidateSuggestion):
    id: str
    cost_source: Literal["reference", "estimate", "unknown"] = "unknown"


class CandidatePool(BaseModel):
    requirements: Requirements
    places: list[CandidatePlace] = Field(min_length=1, max_length=40)
    warnings: list[str] = Field(default_factory=list)


class AllocationDecision(BaseModel):
    candidate_id: str
    place_name: str
    day: int | None = None
    reason: str


class AllocationReport(BaseModel):
    pool_count: int
    selected_count: int
    pace: Literal["relaxed", "balanced", "full"]
    max_per_day: int
    minutes_per_day: int
    buffer_minutes: int
    decisions: list[AllocationDecision] = Field(default_factory=list)
