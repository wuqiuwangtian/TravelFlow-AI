"""测试替身：假冒的 LLM 与检索 Provider。

用途：让编排测试**完全离线、零额度消耗**，且行为可精确控制。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Any

from app.agents.nodes.collector import CollectResult
from app.agents.nodes.planner import PlanOutput
from app.agents.nodes.refiner import RefineIntent
from app.llm.base import ChatMessage, SchemaT
from app.search.base import SearchResult


class FakeLLM:
    """按 schema 名称返回预置结果的假 LLM。"""

    name = "fake-llm"

    def __init__(
        self,
        *,
        collect: CollectResult | None = None,
        plan: PlanOutput | None = None,
        intent: RefineIntent | None = None,
        refine_text: str = "# 修改后的路书",
        delay: float = 0.0,
    ) -> None:
        self._collect = collect
        self._plan = plan
        self._intent = intent
        self._refine_text = refine_text
        self._delay = delay
        self.calls: list[str] = []

    async def chat(
        self, messages: Sequence[ChatMessage], *, temperature: float | None = None
    ) -> str:
        self.calls.append("chat")
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._refine_text

    async def structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[SchemaT],
        *,
        temperature: float | None = None,
    ) -> SchemaT:
        self.calls.append(f"structured:{schema.__name__}")
        if self._delay:
            await asyncio.sleep(self._delay)
        if schema is CollectResult:
            assert self._collect is not None, "测试未提供 collect 结果"
            return self._collect  # type: ignore[return-value]
        if schema is PlanOutput:
            assert self._plan is not None, "测试未提供 plan 结果"
            return self._plan  # type: ignore[return-value]
        if schema is RefineIntent:
            assert self._intent is not None, "测试未提供 intent 结果"
            return self._intent  # type: ignore[return-value]
        raise AssertionError(f"未预期的 schema: {schema}")

    async def stream(
        self, messages: Sequence[ChatMessage], *, temperature: float | None = None
    ) -> AsyncIterator[str]:
        yield await self.chat(messages)

    async def health_check(self) -> tuple[bool, str]:
        return True, "fake ok"


class FakeSearch:
    """假检索 Provider：记录调用、可注入延迟、可指定失败的分支。"""

    name = "fake-search"
    requires_api_key = False

    def __init__(
        self,
        *,
        results_per_query: int = 2,
        delay: float = 0.0,
        fail_queries: Sequence[str] = (),
        fail_everything: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self._results_per_query = results_per_query
        self._delay = delay
        self._fail_queries = tuple(fail_queries)
        self._fail_everything = fail_everything

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        domains: Sequence[str] | None = None,
    ) -> list[SearchResult]:
        self.calls.append(query)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail_everything or any(f in query for f in self._fail_queries):
            from app.core.errors import SearchUnavailable

            raise SearchUnavailable(detail=f"fake failure for {query}")

        return [
            SearchResult(
                title=f"结果 {i} for {query[:12]}",
                url=f"https://example.com/{abs(hash(query)) % 10000}/{i}",
                content=f"内容 {i}",
                provider=self.name,
                published_at=datetime(2026, 1, 1),
            )
            for i in range(min(self._results_per_query, max_results))
        ]


def make_collect_result(**requirements: Any) -> CollectResult:
    from app.domain.models import Requirements

    return CollectResult(
        extracted=Requirements(**requirements),
        reply="好的，正在为您检索…",
        ready=True,
    )
