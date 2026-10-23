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


class CareerSource(BaseModel):
    session_id: str
    session_title: str
    session_archived: bool
    branch_id: str
    branch_title: str
    version_id: str
    title: str
    ended_at: datetime


class CareerTrip(CareerFacts, CareerSource):
    cities: list[str]
    finished_branch_count: int
    source_basis: Literal["primary", "latest_finished"]
    cover_photo_id: str | None
    photo_count: int = 0


class FootprintLocation(BaseModel):
    id: str
    name: str
    address: str
    longitude: float
    latitude: float


class CareerFootprint(CareerSource):
    day: int
    activity_id: str
    activity_title: str
    place_name: str
    city: str | None
    location: FootprintLocation | None


class CareerFootprints(BaseModel):
    items: list[CareerFootprint]
    total: int
    located_count: int
    unlocated_count: int
    map_points: list[CareerFootprint]
    map_fingerprint: str


class CareerMemory(CareerSource):
    photo_id: str
    caption: str
    day: int | None
    activity_id: str | None
    activity_title: str | None


class CareerMemories(BaseModel):
    items: list[CareerMemory]
    total: int


class TravelCareer(BaseModel):
    summary: CareerSummary
    cities: list[CareerCity]
    items: list[CareerTrip]
    total: int
