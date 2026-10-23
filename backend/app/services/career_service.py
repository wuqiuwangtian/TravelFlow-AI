"""只统计每次旅行选定方案的当前记录，不合并备选方案的事实。"""

from collections import Counter

from app.domain.career import CareerCity, CareerSummary, CareerTrip, TravelCareer
from app.domain.itinerary import Itinerary, LedgerSummary
from app.storage.session_store import SessionStore


def _trip(row: dict) -> CareerTrip:
    itinerary = Itinerary.model_validate_json(row["itinerary"])
    assert itinerary.ended_at is not None
    completed = [
        a
        for day in itinerary.days
        for a in day.activities
        if a.progress == "completed" and not a.is_context_anchor
    ]
    city_counts: Counter[str] = Counter()
    unlocated = 0
    for activity in completed:
        # 交通或车站完成不代表在当地游玩、停留。
        if activity.kind in ("station", "transport"):
            continue
        city = (activity.place.city if activity.place else "").strip() or activity.city.strip()
        if city.endswith("市") and len(city) > 2:
            city = city[:-1]
        if city:
            city_counts[city] += 1
        else:
            unlocated += 1
    recap = itinerary.recap
    return CareerTrip(
        **{
            key: row[key]
            for key in (
                "session_id",
                "session_title",
                "branch_id",
                "branch_title",
                "version_id",
                "finished_branch_count",
            )
        },
        session_archived=bool(row["session_archived"]),
        title=recap.title if recap else row["session_title"],
        ended_at=itinerary.ended_at,
        cities=sorted(city_counts),
        source_basis="primary" if row["is_primary"] else "latest_finished",
        cover_photo_id=recap.cover_photo_id if recap else None,
        completed_count=len(completed),
        unlocated_completed_count=unlocated,
        expense_count=len(itinerary.expenses),
        unsettled_count=sum(a.status == "pending" for a in itinerary.adjustments),
        ledger=itinerary.ledger_summary,
    )


async def travel_career(
    store: SessionStore, *, page: int, page_size: int, city: str | None
) -> TravelCareer:
    rows = await store.list_career_sources()
    trips = [_trip(row) for row in rows]
    summary = CareerSummary(trip_count=len(trips))
    cities: Counter[str] = Counter()
    for trip in trips:
        for field in (
            "completed_count",
            "unlocated_completed_count",
            "expense_count",
            "unsettled_count",
        ):
            setattr(summary, field, getattr(summary, field) + getattr(trip, field))
        summary.trips_without_expenses += trip.expense_count == 0
        for field in LedgerSummary.model_fields:
            setattr(
                summary.ledger, field, getattr(summary.ledger, field) + getattr(trip.ledger, field)
            )
        cities.update(trip.cities)
    # 城市数量按旅行去重；不把同城多个活动当成多次旅行。
    summary.city_count = len(cities)
    filtered = [trip for trip in trips if city is None or city in trip.cities]
    return TravelCareer(
        summary=summary,
        cities=[
            CareerCity(name=name, trip_count=count)
            for name, count in sorted(cities.items(), key=lambda item: (-item[1], item[0]))
        ],
        items=filtered[(page - 1) * page_size : page * page_size],
        total=len(filtered),
    )
