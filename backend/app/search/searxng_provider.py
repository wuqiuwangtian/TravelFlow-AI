"""自建 SearXNG 检索 Provider（无第三方限额，检索完全自控）。

官方文档：https://docs.searxng.org/
需要用户自行部署 SearXNG 并**开启 JSON 输出**（``settings.yml`` 中
``search.formats`` 需包含 ``json``），否则接口返回 403。
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.logging import get_logger
from app.search.base import HttpSearchProvider, SearchResult

logger = get_logger(__name__)


class SearXNGProvider(HttpSearchProvider):
    name = "searxng"
    requires_api_key = False

    def __init__(self, base_url: str, *, timeout: int = 20, language: str = "zh-CN") -> None:
        super().__init__(timeout=timeout)
        self._base_url = base_url.rstrip("/")
        self._language = language

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        domains: Sequence[str] | None = None,
    ) -> list[SearchResult]:
        params: dict[str, str | int] = {
            "q": query,
            "format": "json",
            "language": self._language,
            "safesearch": 0,
        }
        # 用 SearXNG 的语法表达域名限定，而不是依赖服务端参数
        if domains:
            site_filter = " OR ".join(f"site:{d}" for d in domains)
            params["q"] = f"{query} ({site_filter})"

        try:
            async with self._client() as client:
                response = await client.get(f"{self._base_url}/search", params=params)
        except Exception as exc:
            raise self._fail(
                f"{type(exc).__name__}: {exc}（请确认 SearXNG 已启动且可从本机访问）"
            ) from exc

        self._check(response)

        try:
            body = response.json()
        except Exception as exc:
            raise self._fail(
                f"响应不是合法 JSON（请确认 SearXNG 已开启 json 输出格式）: {exc}"
            ) from exc

        results: list[SearchResult] = []
        for item in (body.get("results") or [])[:max_results]:
            url = item.get("url", "")
            if not url:
                continue
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=url,
                    content=item.get("content") or item.get("snippet") or "",
                    provider=self.name,
                )
            )
        return results
