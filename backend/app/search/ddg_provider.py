"""DuckDuckGo 检索 Provider（零配置、无需任何 Key）。

用途：让用户**不注册任何服务**就能先把项目跑起来（规划 §7.3）。
注意其稳定性不如商业 API，因此定位是「兜底 / 试用」，不是默认。

实现要点：``ddgs`` 是同步库，必须放到线程池执行，
否则会像旧项目一样阻塞事件循环（阶段① P1-1）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from app.core.logging import get_logger
from app.search.base import SearchProvider, SearchResult

logger = get_logger(__name__)


class DuckDuckGoProvider(SearchProvider):
    name = "ddg"
    requires_api_key = False

    def __init__(self, *, timeout: int = 20, region: str = "cn-zh") -> None:
        self._timeout = timeout
        self._region = region

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        domains: Sequence[str] | None = None,
    ) -> list[SearchResult]:
        if domains:
            site_filter = " OR ".join(f"site:{d}" for d in domains)
            query = f"{query} ({site_filter})"

        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(self._search_sync, query, max_results),
                timeout=self._timeout * 2,
            )
        except TimeoutError as exc:
            raise self._fail("请求超时") from exc
        except Exception as exc:
            raise self._fail(f"{type(exc).__name__}: {exc}") from exc

        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("href", ""),
                content=item.get("body", ""),
                provider=self.name,
            )
            for item in raw
            if item.get("href")
        ]

    def _search_sync(self, query: str, max_results: int) -> list[dict]:
        from ddgs import DDGS

        with DDGS(timeout=self._timeout) as client:
            return list(client.text(query, region=self._region, max_results=max_results))

    def _fail(self, reason: str):
        from app.core.errors import SearchUnavailable

        return SearchUnavailable(
            "检索服务（DuckDuckGo）暂时不可用，可稍后重试或在设置中更换检索源",
            detail=reason,
        )
