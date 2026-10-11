"""博查 BochaAI 检索 Provider（国内服务）。

官方文档：https://open.bochaai.com/
注意：本项目**不登记**其计费与额度信息 —— 那类信息会变动，请以官方为准。
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.logging import get_logger
from app.search.base import HttpSearchProvider, SearchResult

logger = get_logger(__name__)

_ENDPOINT = "https://api.bochaai.com/v1/web-search"


class BochaProvider(HttpSearchProvider):
    name = "bocha"
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
        # 博查不支持域名白名单参数，``domains`` 被忽略并降级为普通检索（规划 §7.4）
        payload = {"query": query, "count": max_results, "summary": True}

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
            body = response.json()
        except Exception as exc:
            raise self._fail(f"响应不是合法 JSON: {exc}") from exc

        pages = (body.get("data") or {}).get("webPages", {}).get("value", []) or []
        results: list[SearchResult] = []
        for item in pages:
            url = item.get("url") or item.get("siteUrl") or ""
            if not url:
                continue
            results.append(
                SearchResult(
                    title=item.get("name", ""),
                    url=url,
                    content=item.get("summary") or item.get("snippet") or "",
                    provider=self.name,
                )
            )
        return results
