"""检索 Provider 抽象（规划 §7）。

关键设计 —— **引用可信链路的起点**：
``SearchResult.url`` 由检索服务返回、由程序原样持有，
**全程不经过大模型**，这是「防幻觉」最根本的保证（规划 §19.3）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

import httpx

from app.core.errors import SearchUnavailable


@dataclass(frozen=True, slots=True)
class SearchResult:
    """一条检索结果。``url`` 是**程序透传**的原始链接，非模型生成。"""

    title: str
    url: str
    content: str
    provider: str
    published_at: datetime | None = None

    @property
    def domain(self) -> str:
        """用于展示与去重的域名。"""
        try:
            return httpx.URL(self.url).host or ""
        except Exception:  # pragma: no cover - 防御性
            return ""


@runtime_checkable
class SearchProvider(Protocol):
    """检索服务接口。"""

    name: str
    requires_api_key: bool

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        domains: Sequence[str] | None = None,
    ) -> list[SearchResult]:
        """执行一次检索。

        ``domains`` 为可选的域名白名单：Provider 若不支持则**忽略并降级**，
        绝不因过滤过严而返回空结果（规划 §7.4）。
        """
        ...


class HttpSearchProvider:
    """基于 HTTP 的检索 Provider 公共基类：统一超时、连接复用与错误映射。"""

    name: str = "base"
    requires_api_key: bool = True

    def __init__(self, *, timeout: int = 20) -> None:
        self._timeout = timeout

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={"User-Agent": "TravelFlow-AI/0.1 (+local travel assistant)"},
        ) as client:
            yield client

    def _fail(self, reason: str) -> SearchUnavailable:
        """构造面向用户的检索失败异常。"""
        return SearchUnavailable(
            f"检索服务（{self.name}）暂时不可用，请检查网络或稍后重试",
            detail=reason,
        )

    def _check(self, response: httpx.Response) -> None:
        if response.status_code in (401, 403):
            raise SearchUnavailable(
                f"检索服务（{self.name}）鉴权失败，请检查 API Key 是否正确",
                detail=f"status={response.status_code}",
            )
        if response.status_code >= 400:
            raise self._fail(f"status={response.status_code} body={response.text[:200]}")
