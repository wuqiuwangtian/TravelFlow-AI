"""隔离模拟几何数据，不代表真实景点位置，不请求外部地图。"""

import asyncio

from candidate_fixtures import candidate_draft

from app.core.errors import ProviderError
from app.domain.itinerary import Place, RouteLeg
from app.maps.matching import distance_meters


class GeoMaps:
    def __init__(self, mode="complete", calls=None):
        self.mode = mode
        self.calls = calls if calls is not None else {"search": [], "route": []}

    async def search_places(self, keyword, city):
        self.calls["search"].append(keyword)
        names = [p.place_name for p in candidate_draft().places]
        index = names.index(keyword)
        region = min(index // 3, 2)
        place = Place(
            id=f"geo-{index}",
            name=keyword,
            address=f"北京模拟区域{region}路{index}号",
            city="北京",
            district=["西城区", "海淀区", "朝阳区"][region],
            typecode="110000",
            longitude=116.1 + region * 0.2 + (index % 3) * 0.002,
            latitude=39.9 + (index % 3) * 0.002,
        )
        if keyword == "北海公园":
            if self.mode == "ambiguous":
                return [
                    place.model_copy(update={"id": "north-east", "address": "北京东侧分园"}),
                    place.model_copy(
                        update={"id": "north-west", "address": "北京西侧分园", "longitude": 116.7}
                    ),
                ]
            if self.mode == "stale":
                return [place.model_copy(update={"id": "north-new"})]
            if self.mode == "missing":
                return []
            if self.mode == "unavailable":
                raise ProviderError("private-map-test-marker")
            if self.mode == "unknown_city":
                return [place.model_copy(update={"city": ""})]
            if self.mode == "cross_city":
                return [place.model_copy(update={"city": "天津"})]
            if self.mode == "timeout":
                await asyncio.sleep(2)
        return [place]

    async def route(self, origin, destination, mode):
        self.calls["route"].append((origin.id, destination.id))
        return RouteLeg(
            from_id=origin.id,
            to_id=destination.id,
            mode=mode,
            status="ok",
            distance_meters=distance_meters(origin, destination),
            duration_seconds=600,
            cost=2,
            cost_basis="total",
            cost_source="reference",
        )
