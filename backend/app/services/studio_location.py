"""独立候选定位：只接收地图结果，按项降级，地点选择必须仍存在于新结果。"""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import Settings
from app.core.errors import ProviderError
from app.domain.itinerary import ScheduledActivity
from app.domain.studio_candidates import CandidateLocation, CandidatePool, PoolLocationReport
from app.domain.workbench import StudioNode
from app.maps.amap import AmapProvider
from app.maps.matching import choose_place, normal_name


def same_city(first: str, second: str) -> bool:
    return bool(first.strip() and second.strip()) and normal_name(
        first.strip().removesuffix("市")
    ) == normal_name(second.strip().removesuffix("市"))


def fresh_locations(stamp: datetime | None) -> bool:
    if stamp is None:
        return False
    stamp = stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    return timedelta(0) <= datetime.now(UTC) - stamp <= timedelta(hours=24)


async def locate_candidates(
    node: StudioNode, pool: CandidatePool, settings: Settings
) -> CandidatePool:
    result = pool.model_copy(deep=True)
    for p in result.places:
        p.location = CandidateLocation(
            status="unavailable", reason="候选定位尚未完成，未使用旧坐标。"
        )
    if not settings.amap_api_key:
        for p in result.places:
            p.location = CandidateLocation(
                status="unconfigured", reason="地图未配置，保留候选名称与原建议。"
            )
            p.location.selected_id = node.parameters.location_choices.get(p.id)
        result.location = PoolLocationReport(
            status="unconfigured", warnings=["地图未配置，地理分组将沿用时长方案。"]
        )
        return result
    semaphore = asyncio.Semaphore(3)
    async with httpx.AsyncClient(timeout=min(settings.amap_timeout_seconds, 8)) as client:
        provider = AmapProvider(client, settings.amap_api_key)

        async def locate(index: int) -> None:
            p = result.places[index]
            async with semaphore:
                try:
                    async with asyncio.timeout(min(settings.amap_timeout_seconds, 12)):
                        found = (await provider.search_places(p.place_name, p.city))[:8]
                    choice = node.parameters.location_choices.get(p.id)
                    if choice:
                        chosen = next((place for place in found if place.id == choice), None)
                        reason = (
                            "沿用你指定且仍在本次地图结果中的地点。"
                            if chosen
                            else "原指定地点已不在本次地图结果中，请重新选择或清除指定。"
                        )
                    else:
                        chosen, reason = choose_place(
                            ScheduledActivity(
                                id=p.id,
                                title=p.place_name,
                                place_name=p.place_name,
                                city=p.city,
                                kind="attraction",
                            ),
                            found,
                        )
                    if chosen and not same_city(chosen.city, p.city):
                        chosen, reason = None, "地图城市与候选不一致或不明确，未使用坐标。"
                    p.location = CandidateLocation(
                        status="matched" if chosen else "ambiguous" if found else "not_found",
                        place=chosen,
                        candidates=found,
                        reason=reason,
                        checked_at=datetime.now(UTC),
                    )
                except (
                    ProviderError,
                    httpx.HTTPError,
                    ValueError,
                    KeyError,
                    TypeError,
                    TimeoutError,
                ):
                    p.location = CandidateLocation(
                        status="unavailable",
                        reason="地图查询失败或超时，保留候选建议，可重跑定位节点。",
                        checked_at=datetime.now(UTC),
                    )

        try:
            async with asyncio.timeout(45):
                await asyncio.gather(*(locate(i) for i in range(len(result.places))))
        except TimeoutError:
            for p in result.places:
                if p.location and p.location.checked_at is None:
                    p.location.reason = "批量定位超时，此项未完成，可重跑定位节点。"
    for p in result.places:
        if p.location:
            p.location.selected_id = node.parameters.location_choices.get(p.id)
    ready = all(p.location and p.location.status == "matched" for p in result.places)
    result.location = PoolLocationReport(
        status="ready" if ready else "partial",
        checked_at=datetime.now(UTC),
        warnings=[
            "定位仅确认地图地点，不证明营业、预约或票价；24小时内可供分日和路线节点复用，重新定位请重跑本节点。"
        ],
    )
    return result
