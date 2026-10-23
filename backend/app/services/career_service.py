"""只统计每次旅行选定方案的当前记录，不合并备选方案的事实。"""

import hashlib
import json
from collections import Counter

from app.core.config import Settings
from app.core.errors import ConflictError, InvalidRequest, ProviderError
from app.domain.career import (
    CareerCity,
    CareerFootprint,
    CareerFootprints,
    CareerMemories,
    CareerMemory,
    CareerSource,
    CareerSummary,
    CareerTrip,
    FootprintLocation,
    TravelCareer,
)
from app.domain.itinerary import DayPlan, Itinerary, LedgerSummary, Place, ScheduledActivity
from app.maps.assets import map_asset
from app.storage.session_store import SessionStore


def _city(activity: ScheduledActivity) -> str:
    city = (activity.place.city if activity.place else "").strip() or activity.city.strip()
    return city[:-1] if city.endswith("市") and len(city) > 2 else city


def _visited(activity: ScheduledActivity) -> bool:
    return (
        activity.progress == "completed"
        and not activity.is_context_anchor
        and activity.kind not in ("station", "transport")
    )


def _trip(row: dict, itinerary: Itinerary) -> CareerTrip:
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
        city = _city(activity)
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
        photo_count=sum(photo.included for photo in recap.photos) if recap else 0,
        completed_count=len(completed),
        unlocated_completed_count=unlocated,
        expense_count=len(itinerary.expenses),
        unsettled_count=sum(a.status == "pending" for a in itinerary.adjustments),
        ledger=itinerary.ledger_summary,
    )


async def _sources(store: SessionStore) -> list[tuple[CareerTrip, Itinerary]]:
    sources = []
    for row in await store.list_career_sources():
        itinerary = Itinerary.model_validate_json(row["itinerary"])
        sources.append((_trip(row, itinerary), itinerary))
    return sources


def _source(trip: CareerTrip) -> dict:
    return trip.model_dump(include=set(CareerSource.model_fields))


async def travel_footprints(
    store: SessionStore, *, page: int, page_size: int, city: str | None
) -> CareerFootprints:
    footprints = []
    for trip, itinerary in await _sources(store):
        for day in itinerary.days:
            for activity in day.activities:
                if not _visited(activity) or (city is not None and _city(activity) != city):
                    continue
                place = activity.place if activity.place_status == "matched" else None
                footprints.append(
                    CareerFootprint(
                        **_source(trip),
                        day=day.day,
                        activity_id=activity.id,
                        activity_title=activity.title,
                        place_name=activity.place_name or activity.title,
                        city=_city(activity) or None,
                        location=FootprintLocation.model_validate(place.model_dump())
                        if place
                        else None,
                    )
                )
    # 同一地点的反复到访保留来源记录，地图只取前10个不同位置，不连成GPS轨迹。
    markers: dict[tuple[float, float], CareerFootprint] = {}
    for footprint in footprints:
        if footprint.location and len(markers) < 10:
            location = footprint.location
            markers.setdefault(
                (round(location.longitude, 6), round(location.latitude, 6)), footprint
            )
    points = list(markers.values())
    fingerprint = hashlib.sha256(
        json.dumps(
            [point.model_dump(mode="json") for point in points],
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    located = sum(item.location is not None for item in footprints)
    return CareerFootprints(
        items=footprints[(page - 1) * page_size : page * page_size],
        total=len(footprints),
        located_count=located,
        unlocated_count=len(footprints) - located,
        map_points=points,
        map_fingerprint=fingerprint,
    )


async def career_map(
    store: SessionStore, settings: Settings, *, city: str | None, snapshot: str
) -> tuple[bytes, str]:
    footprints = await travel_footprints(store, page=1, page_size=10, city=city)
    if snapshot != footprints.map_fingerprint:
        raise ConflictError("足迹记录已变化，请刷新足迹后再查看地图。")
    if not footprints.map_points:
        raise InvalidRequest("还没有已完成且坐标已确认的地点，实际记录仍可查看。")
    if not settings.amap_api_key:
        raise InvalidRequest("尚未配置高德地图服务，已保留到访记录和地点坐标。")
    # 不传原计划交通或 polyline：只展示已完成地点的静态点位。
    day = DayPlan(
        day=1,
        activities=[
            ScheduledActivity(
                id=point.activity_id,
                title=point.activity_title,
                place=Place(**point.location.model_dump()),
            )
            for point in footprints.map_points
            if point.location is not None
        ],
    )
    try:
        return await map_asset("career-" + snapshot, day, settings)
    except ProviderError:
        raise ProviderError(
            "足迹地图暂时无法加载，已保留到访记录；可稍后重试或检查地图服务配置。"
        ) from None


async def travel_memories(
    store: SessionStore, *, page: int, page_size: int, city: str | None
) -> CareerMemories:
    memories = []
    for trip, itinerary in await _sources(store):
        # 城市筛选采用来源旅行的实际到访城市，照片标注不用于证明到访。
        if (city is not None and city not in trip.cities) or itinerary.recap is None:
            continue
        activities = {
            a.id: a for day in itinerary.days for a in day.activities if not a.is_context_anchor
        }
        photos = [photo for photo in itinerary.recap.photos if photo.included]
        # 天数仅为用户标注的行程日；不从计划出发日、结束时间或 EXIF 猜拍摄日期。
        photos.sort(key=lambda photo: photo.day if photo.day is not None else 61)
        for photo in photos:
            activity = activities.get(photo.activity_id or "")
            memories.append(
                CareerMemory(
                    **_source(trip),
                    photo_id=photo.id,
                    caption=photo.caption,
                    day=photo.day,
                    activity_id=photo.activity_id,
                    activity_title=activity.title if activity else None,
                )
            )
    return CareerMemories(
        items=memories[(page - 1) * page_size : page * page_size], total=len(memories)
    )


async def travel_career(
    store: SessionStore, *, page: int, page_size: int, city: str | None
) -> TravelCareer:
    trips = [trip for trip, _ in await _sources(store)]
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
