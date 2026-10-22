"""高德 Web API；有界重试、请求内缓存和不包含密钥的错误信息。"""

from __future__ import annotations

import asyncio
import math
import re

import httpx

from app.core.errors import ProviderError
from app.domain.itinerary import DayPlan, Place, RouteLeg, TravelMode


def _number(value: object) -> float | None:
    try:
        number = float(str(value))
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def _place(data: dict) -> Place:
    longitude, latitude = map(float, data["location"].split(","))

    def string(key: str) -> str:
        value = data.get(key)
        return value if isinstance(value, str) else ""

    return Place(
        id=data["id"],
        name=data["name"],
        address=string("address"),
        longitude=longitude,
        latitude=latitude,
        citycode=string("citycode"),
        city=string("cityname"),
        district=string("adname"),
        typecode=string("typecode"),
    )


def _provider_error(data: dict | None = None) -> ProviderError:
    message = {
        "10001": "高德 Key 无效或已失效，请在设置中更新地图 Key。",
        "10002": "当前高德 Key 尚未开通此项服务。",
        "10003": "高德今日调用额度已用完，行程仍可查看。",
        "10004": "高德请求暂时过于频繁，已保留行程，可稍后自动重试。",
        "10009": "高德 Key 类型不匹配，请使用 Web 服务类型的 Key。",
        "10012": "当前高德 Key 缺少此项服务权限。",
    }.get(str((data or {}).get("infocode")), "高德服务暂时不可用，已保留行程，请稍后重试。")
    return ProviderError(message)


class AmapProvider:
    def __init__(self, client: httpx.AsyncClient, key: str) -> None:
        self.client = client
        self._key = key
        self._cache: dict[tuple, dict] = {}
        self._lock = asyncio.Lock()

    async def _json(self, path: str, params: dict) -> dict:
        cache_key = (path, tuple(sorted(params.items())))
        async with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
            for attempt in range(2):
                try:
                    response = await self.client.get(
                        f"https://restapi.amap.com{path}", params={**params, "key": self._key}
                    )
                    if (
                        response.status_code == 429 or response.status_code >= 500
                    ) and attempt == 0:
                        await asyncio.sleep(0.4)
                        continue
                    response.raise_for_status()
                    data = response.json()
                    if not isinstance(data, dict):
                        raise ValueError("invalid response")
                    if str(data.get("status")) != "1":
                        if attempt == 0 and (
                            str(data.get("infocode")) in {"10004", "10016", "10021"}
                            or "QPS" in str(data.get("info", ""))
                        ):
                            await asyncio.sleep(0.6)
                            continue
                        raise _provider_error(data) from None
                    self._cache[cache_key] = data
                    return data
                except (httpx.HTTPError, ValueError, TypeError):
                    if attempt == 0:
                        await asyncio.sleep(0.3)
                        continue
                    raise _provider_error() from None
        raise _provider_error()

    async def search_places(self, keyword: str, city: str) -> list[Place]:
        data = await self._json(
            "/v5/place/text",
            {
                "keywords": keyword[:80],
                "region": city,
                "city_limit": "false",
                "page_size": 8,
            },
        )
        places = []
        for item in data.get("pois", []):
            try:
                places.append(_place(item))
            except (KeyError, ValueError, TypeError, AttributeError):
                continue
        return places

    async def get_place(self, place_id: str) -> Place:
        data = await self._json("/v5/place/detail", {"id": place_id})
        try:
            return _place(data["pois"][0])
        except (KeyError, IndexError, ValueError, TypeError):
            raise ProviderError("该地点暂时不可用，原行程已保留。") from None

    async def route(self, origin: Place, destination: Place, mode: TravelMode) -> RouteLeg:
        if mode not in {"walking", "transit", "driving"}:
            raise ProviderError("城际交通由行程资料安排，不使用市内路线接口。")
        params = {"origin": origin.location, "destination": destination.location}
        if mode == "transit":
            params.update(
                {"city": origin.citycode, "cityd": destination.citycode, "extensions": "all"}
            )
            data = await self._json("/v3/direction/transit/integrated", params)
            paths = data.get("route", {}).get("transits", [])
        else:
            params["show_fields"] = "cost,polyline"
            data = await self._json(f"/v5/direction/{mode}", params)
            paths = data.get("route", {}).get("paths", [])
        if not paths:
            raise ProviderError("暂未查询到这段路线。")
        path = paths[0]
        cost = path.get("cost") or {}
        duration = _number(path.get("duration") if mode == "transit" else cost.get("duration"))
        distance = _number(path.get("distance"))
        if duration is None or distance is None:
            raise ProviderError("高德尚未返回完整的路线时间，已保留建议安排。")
        walking = (
            distance
            if mode == "walking"
            else (_number(path.get("walking_distance")) if mode == "transit" else 0)
        )
        fare = _number(cost) if mode == "transit" else (0 if mode == "walking" else None)
        chunks = [step.get("polyline", "") for step in path.get("steps", [])]
        if mode == "transit":
            for segment in path.get("segments", []):
                chunks.extend(
                    s.get("polyline", "") for s in segment.get("walking", {}).get("steps", [])
                )
                chunks.extend(
                    s.get("polyline", "") for s in segment.get("bus", {}).get("buslines", [])
                )
        points = [
            p
            for chunk in chunks
            for p in chunk.split(";")
            if re.fullmatch(r"-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?", p)
        ]
        if len(points) > 60:
            points = [points[round(i * (len(points) - 1) / 59)] for i in range(60)]
        return RouteLeg(
            from_id="",
            to_id="",
            mode=mode,
            status="ok",
            distance_meters=distance,
            duration_seconds=duration,
            walking_meters=walking,
            cost=fare,
            polyline=points,
            cost_basis="per_person" if mode == "transit" else "total",
            cost_source="reference" if fare is not None else "unknown",
        )

    async def static_map(self, day: DayPlan) -> tuple[bytes, str]:
        located = [(i, a.place) for i, a in enumerate(day.activities, 1) if a.place]
        if not located:
            raise ProviderError("正在使用行程连线图，具体地点完善后会显示地图。")
        params = {
            "key": self._key,
            "size": "750*420",
            "markers": "small,0x14b8a6,:" + ";".join(p.location for _, p in located[:10]),
            "labels": "|".join(
                f"{i},0,1,14,0xFFFFFF,0x0d9488:{p.location}" for i, p in located[:10]
            ),
        }
        paths = []
        for leg in day.legs:
            if leg.status == "ok" and leg.polyline:
                points = leg.polyline
                if len(points) > 20:
                    points = [points[round(i * (len(points) - 1) / 19)] for i in range(20)]
                paths.append(";".join(points))
        if paths:
            params["paths"] = "|".join(f"4,0x0d9488,1,,:{path}" for path in paths[:4])
        for attempt in range(2):
            try:
                response = await self.client.get(
                    "https://restapi.amap.com/v3/staticmap", params=params
                )
                response.raise_for_status()
                content = response.content
                if content.startswith(b"\x89PNG\r\n\x1a\n"):
                    return content, "image/png"
                if content.startswith(b"\xff\xd8\xff"):
                    return content, "image/jpeg"
                try:
                    data = response.json()
                    if isinstance(data, dict) and data.get("infocode"):
                        raise _provider_error(data) from None
                except ValueError:
                    pass
                raise ProviderError("高德没有返回地图图片，已切换为行程连线图。")
            except (httpx.HTTPError, ProviderError) as exc:
                if attempt == 0:
                    params.pop("paths", None)  # 叠加线过长等情况自动退回地点底图
                    await asyncio.sleep(0.3)
                    continue
                if isinstance(exc, ProviderError):
                    raise exc from None
                raise ProviderError("地图服务连接超时，已切换为行程连线图。") from None
        raise _provider_error()
