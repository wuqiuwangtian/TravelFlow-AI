from __future__ import annotations

from typing import Protocol

from app.domain.itinerary import Place, RouteLeg, TravelMode


class MapProvider(Protocol):
    async def search_places(self, keyword: str, city: str) -> list[Place]: ...

    async def get_place(self, place_id: str) -> Place: ...

    async def route(self, origin: Place, destination: Place, mode: TravelMode) -> RouteLeg: ...
