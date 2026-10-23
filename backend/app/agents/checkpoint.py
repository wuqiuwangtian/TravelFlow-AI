"""会话状态持久化（规划 §2.5）。

使用 LangGraph 官方 SQLite Checkpointer，一次解决三个问题：

1. **断点续聊** —— 进程重启后会话不丢（旧项目用进程内 dict，重启即失，阶段① P1-5）；
2. **并发安全** —— 由 SQLite 事务保证，替代旧项目的无锁全局字典；
3. **版本与分支的基础** —— 每个 checkpoint 即一个历史版本，
   M4 的「版本树 / 回到某个版本 / 分支对比」都建立在它之上。

### 关于序列化白名单

状态里存的是自定义类型（``Requirements`` / ``ResearchBundle`` 等）。
LangGraph 的反序列化器对**未登记的类型**会发出警告，并在未来版本中直接拒绝加载。
因此这里显式登记白名单，把「未来会坏」变成「现在就对」。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

# 状态中会用到的自定义类型；新增领域模型时需同步登记
_ALLOWED_JSON_MODULES: list[tuple[str, ...]] = [
    ("app.domain.linked_replan",),
    ("app.domain.clarification",),
    ("app.domain.models",),
    ("app.domain.itinerary",),
    ("app.domain.enums",),
    ("app.search.base",),
]

_ALLOWED_MSGPACK_MODULES: list[tuple[str, ...]] = [
    ("app.domain.itinerary", "TravelRecap"),
    ("app.domain.linked_replan", "ActivityMove"),
    ("app.domain.linked_replan", "MoveChoice"),
    ("app.domain.linked_replan", "MoveClarification"),
    ("app.domain.linked_replan", "LinkedReplanPreview"),
    ("app.domain.linked_replan", "LinkedActivityChange"),
    ("app.domain.itinerary", "LinkedReplanNote"),
    ("app.domain.clarification", "JourneyClarification"),
    ("app.domain.clarification", "PlaceClarification"),
    ("app.domain.clarification", "PlaceTarget"),
    ("app.domain.clarification", "ExpenseTarget"),
    ("app.domain.itinerary", "Itinerary"),
    ("app.domain.itinerary", "DayPlan"),
    ("app.domain.itinerary", "ScheduledActivity"),
    ("app.domain.itinerary", "Place"),
    ("app.domain.itinerary", "RouteLeg"),
    ("app.domain.itinerary", "Expense"),
    ("app.domain.itinerary", "BudgetItem"),
    ("app.domain.itinerary", "ExpenseAdjustment"),
    ("app.domain.itinerary", "Booking"),
    ("app.domain.itinerary", "ReplanContext"),
    ("app.domain.models", "Requirements"),
    ("app.domain.models", "ResearchBundle"),
    ("app.domain.models", "Citation"),
    ("app.domain.models", "TodoItem"),
    ("app.domain.enums", "PlanStatus"),
    ("app.domain.enums", "DetailLevel"),
    ("app.domain.enums", "TodoKind"),
    ("app.search.base", "SearchResult"),
]


def build_serde() -> JsonPlusSerializer:
    """构造带白名单的序列化器。"""
    return JsonPlusSerializer(
        allowed_json_modules=_ALLOWED_JSON_MODULES,
        allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES,
    )


@asynccontextmanager
async def open_checkpointer(db_path: str | Path) -> AsyncIterator[AsyncSqliteSaver]:
    """打开一个异步 SQLite checkpointer（自动建表）。

    用法::

        async with open_checkpointer(settings.session_db_path) as saver:
            graph = build_graph(llm, search, checkpointer=saver)
            await graph.ainvoke(state, thread_config(branch_id))
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(str(path))
    try:
        saver = AsyncSqliteSaver(conn, serde=build_serde())
        await saver.setup()
        yield saver
    finally:
        await conn.close()


def thread_config(branch_id: str, **extra: object) -> dict[str, dict[str, object]]:
    """构造某个分支（thread）的调用配置。

    **分支即独立 thread**：各分支状态完全隔离，血缘关系记录在业务表中（规划 §2.5）。

    ``extra`` 会并入 ``configurable``，节点里可经 ``get_config()`` 读到 ——
    用于显式告知节点「本次是流式调用」（见 ``agents/streaming.py``）。
    """
    return {"configurable": {"thread_id": branch_id, **extra}}
