"""百度智能云千帆 AI 搜索 Provider（国内服务）。

官方文档：https://cloud.baidu.com/doc/qianfan/index.html
同样遵循「不登记易变信息」原则，只实现调用本身。
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.logging import get_logger
from app.search.base import HttpSearchProvider, SearchResult

logger = get_logger(__name__)

_ENDPOINT = "https://qianfan.baidubce.com/v2/ai_search/web_search"


class QianfanProvider(HttpSearchProvider):
    name = "qianfan"
    requires_api_key = True

    def __init__(self, api_key: str, *, timeout: int = 20) -> None:
        super().__init__(timeout=timeout)
        self._api_key = api_key

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        domains: Sequence[str] | None = None,
    ) -> list[SearchResult]:
        # 千帆不支持域名白名单参数，``domains`` 降级忽略
        payload: dict[str, object] = {
            "messages": [{"role": "user", "content": query}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": max_results}],
        }
        if domains:
            payload["search_filter"] = {"match": {"site": list(domains)}}

        try:
            async with self._client() as client:
                response = await client.post(
                    _ENDPOINT,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                )
        except Exception as exc:
            raise self._fail(f"{type(exc).__name__}: {exc}") from exc

        self._check(response)

        try:
            body = response.json()
        except Exception as exc:
            raise self._fail(f"响应不是合法 JSON: {exc}") from exc

        results: list[SearchResult] = []
        for item in body.get("references", []) or []:
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
