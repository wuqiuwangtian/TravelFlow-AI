"""旅行生涯的只读汇总，不另存一份旅行事实。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.itinerary import LedgerSummary


class CareerFacts(BaseModel):
    completed_count: int = 0
    unlocated_completed_count: int = 0
    expense_count: int = 0
    unsettled_count: int = 0
    ledger: LedgerSummary = Field(default_factory=LedgerSummary)


class CareerSummary(CareerFacts):
    trip_count: int = 0
    city_count: int = 0
    trips_without_expenses: int = 0


class CareerCity(BaseModel):
    name: str
    trip_count: int


class CareerTrip(CareerFacts):
    session_id: str
    session_title: str
    session_archived: bool
    branch_id: str
    branch_title: str
    version_id: str
    title: str
    ended_at: datetime
    cities: list[str]
    finished_branch_count: int
    source_basis: Literal["primary", "latest_finished"]
    cover_photo_id: str | None


class TravelCareer(BaseModel):
    summary: CareerSummary
    cities: list[CareerCity]
    items: list[CareerTrip]
    total: int
