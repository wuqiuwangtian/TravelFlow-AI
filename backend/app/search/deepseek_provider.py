"""DeepSeek 独立网页搜索；原始链接直接进入现有引用链路。

接口说明：https://api-docs.deepseek.com/zh-cn/guides/search/
只使用 Search 的摘要，不额外调用 Fetch，也不触发模型生成。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime

import httpx

from app.core.errors import SearchUnavailable
from app.search.base import HttpSearchProvider, SearchResult

_ENDPOINT = "https://api.deepseek.com/v1/tools/search"
_QUERY_LIMIT = 128


class DeepSeekSearchProvider(HttpSearchProvider):
    name = "deepseek"

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
        query = query.strip()
        if not query:
            raise SearchUnavailable("检索关键词不能为空，请补充目的地或搜索内容。")
        query = query[:_QUERY_LIMIT]
        sites = list(dict.fromkeys(d for d in domains or () if re.fullmatch(r"[\w.-]+", d)))
        suffix = " " + " OR ".join(f"site:{d}" for d in sites) if sites else ""
        # 给关键词保留空间；过长的域名限制直接降级为普通搜索。
        filtered = query[: _QUERY_LIMIT - len(suffix)] + suffix if len(suffix) < 96 else query
        count = max(1, min(max_results, 50))
        results = await self._search(filtered, count)
        if not results and filtered != query:
            # 限定站点无结果时只补一次普通搜索，不把白名单过严当成无旅行资料。
            results = await self._search(query, count)
        return results

    async def _search(self, query: str, count: int) -> list[SearchResult]:
        try:
            async with self._client() as client:
                response = await client.post(
                    _ENDPOINT,
                    json={"query": query, "count": count, "need_content": False},
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.HTTPError as exc:
            # 不保留请求对象、鉴权头或第三方原始异常中的敏感内容。
            raise self._fail(type(exc).__name__) from exc
        self._check(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise self._fail("响应不是合法 JSON") from exc
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise self._fail("响应缺少 results 数组")
        results: list[SearchResult] = []
        seen: set[str] = set()
        for item in body["results"]:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or url in seen or not self._web_url(url):
                continue
            seen.add(url)
            content = next(
                (
                    item[k].strip()
                    for k in ("summary", "snippet", "content")
                    if isinstance(item.get(k), str) and item[k].strip()
                ),
                "",
            )
            published = None
            if isinstance(item.get("date"), str):
                with suppress(ValueError):
                    published = datetime.fromisoformat(item["date"])
            results.append(
                SearchResult(
                    title=item["title"] if isinstance(item.get("title"), str) else "",
                    url=url,
                    content=content[:8000],
                    provider=self.name,
                    published_at=published,
                )
            )
            if len(results) == count:
                break
        if body["results"] and not results:
            raise self._fail("响应未包含可用的网页链接")
        return results

    @staticmethod
    def _web_url(value: str) -> bool:
        try:
            url = httpx.URL(value)
            return bool(url.scheme in {"http", "https"} and url.host and not url.userinfo)
        except (httpx.InvalidURL, ValueError):
            return False

    def _check(self, response: httpx.Response) -> None:
        messages = {
            401: "DeepSeek 网页搜索鉴权失败，请检查 DeepSeek 官方 API Key。",
            403: "DeepSeek 网页搜索访问被拒绝，请检查 API Key 的权限。",
            402: "DeepSeek 账户余额不足，请检查账户余额后重试。",
            429: "DeepSeek 网页搜索请求过于频繁，请稍后重试。",
        }
        if response.status_code in messages:
            raise SearchUnavailable(
                messages[response.status_code], detail=f"status={response.status_code}"
            )
        if response.status_code >= 400:
            raise self._fail(f"status={response.status_code}")
