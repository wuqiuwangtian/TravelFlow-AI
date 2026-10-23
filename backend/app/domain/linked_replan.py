"""相邻两天调整的待应用方案，与正式旅程分开保存。"""

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.itinerary import ClockTime, Itinerary


class LinkedReplanInput(BaseModel):
    first_day: int = Field(ge=1, le=59)
    rest_minutes: int = Field(default=480, ge=0, le=1440)
    next_departure: ClockTime | None = None


class LinkedActivityChange(BaseModel):
    day: int
    title: str
    before: str
    after: str
    status: Literal["kept", "shifted", "omitted"]
    protected: bool = False


class LinkedReplanPreview(BaseModel):
    kind: Literal["linked_replan"] = "linked_replan"
    itinerary: Itinerary
    prompt: str
    changes: list[LinkedActivityChange]
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    itinerary_fingerprint: str
    after_message_id: str = ""
