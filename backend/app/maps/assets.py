"""不可变版本的地图短期缓存，合并并发请求，避免切换视图重复打高德。"""

import asyncio
import time
from collections import OrderedDict
from weakref import WeakValueDictionary

import httpx

from app.core.config import Settings
from app.core.errors import ProviderError
from app.domain.itinerary import DayPlan
from app.maps.amap import AmapProvider

_cache: OrderedDict[tuple[str, int, int], tuple[float, bytes, str]] = OrderedDict()
_locks: WeakValueDictionary[tuple[str, int, int], asyncio.Lock] = WeakValueDictionary()


async def map_asset(version_id: str, day: DayPlan, settings: Settings) -> tuple[bytes, str]:
    key = (version_id, day.day, hash(settings.amap_api_key))
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        item = _cache.get(key)
        if item and time.monotonic() - item[0] < 300:
            _cache.move_to_end(key)
            return item[1], item[2]
        try:
            async with asyncio.timeout(18):
                async with httpx.AsyncClient(timeout=httpx.Timeout(7, connect=4)) as client:
                    image, mime = await AmapProvider(
                        client, settings.amap_api_key or ""
                    ).static_map(day)
        except TimeoutError:
            raise ProviderError("地图查询超时，行程连线仍可查看。") from None
        if len(image) <= 3 * 1024 * 1024:
            _cache[key] = (time.monotonic(), image, mime)
            while len(_cache) > 24:
                _cache.popitem(last=False)
        return image, mime
