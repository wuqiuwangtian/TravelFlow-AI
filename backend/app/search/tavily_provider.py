"""Tavily 检索 Provider（默认）。

Tavily 专为 LLM 设计，返回结构化结果，且原生支持域名白名单过滤。
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.logging import get_logger
from app.search.base import HttpSearchProvider, SearchResult

logger = get_logger(__name__)

_ENDPOINT = "https://api.tavily.com/search"


class TavilyProvider(HttpSearchProvider):
    name = "tavily"
    requires_api_key = True

    def __init__(self, api_key: str, *, timeout: int = 20, search_depth: str = "advanced") -> None:
        super().__init__(timeout=timeout)
        self._api_key = api_key
        self._search_depth = search_depth

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        domains: Sequence[str] | None = None,
    ) -> list[SearchResult]:
        payload: dict[str, object] = {
            "query": query,
            "max_results": max_results,
            "search_depth": self._search_depth,
            "include_answer": False,
        }
        # Tavily 原生支持域名过滤
        if domains:
            payload["include_domains"] = list(domains)

        try:
            async with self._client() as client:
                response = await client.post(
                    _ENDPOINT,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except Exception as exc:
            raise self._fail(f"{type(exc).__name__}: {exc}") from exc

        self._check(response)

        try:
            data = response.json()
        except Exception as exc:
            raise self._fail(f"响应不是合法 JSON: {exc}") from exc

        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                content=item.get("content", ""),
                provider=self.name,
            )
            for item in data.get("results", [])
            if item.get("url")
        ]
