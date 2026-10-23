"""相邻两天调整的待应用方案，与正式旅程分开保存。"""

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.itinerary import ClockTime, Itinerary


class ActivityMove(BaseModel):
    activity_id: str = Field(min_length=1, max_length=100)
    source_day: int = Field(ge=1, le=60)
    destination_day: int = Field(ge=1, le=60)
    before_activity_id: str | None = Field(default=None, max_length=100)


class MoveChoice(BaseModel):
    move: ActivityMove
    label: str


class MoveClarification(BaseModel):
    kind: Literal["move_activity"] = "move_activity"
    prompt: str
    choices: list[MoveChoice] = Field(max_length=80)
    original_text: str
    itinerary_fingerprint: str
    after_message_id: str = ""


class LinkedReplanInput(BaseModel):
    first_day: int = Field(ge=1, le=59)
    rest_minutes: int = Field(default=480, ge=0, le=1440)
    next_departure: ClockTime | None = None
    moves: list[ActivityMove] = Field(default_factory=list, max_length=8)


class LinkedActivityChange(BaseModel):
    day: int
    title: str
    before: str
    after: str
    status: Literal["kept", "shifted", "omitted", "moved_in", "moved_out"]
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
