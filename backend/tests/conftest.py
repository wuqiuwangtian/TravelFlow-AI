from __future__ import annotations

import httpx
import pytest_asyncio
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.checkpoint import build_serde
from app.agents.graph import build_graph
from app.agents.itinerary_output import PlanDetails
from app.agents.nodes.collector import CollectResult
from app.agents.nodes.planner import PlanOutput
from app.agents.nodes.refiner import RefineIntent, RevisionOutput
from app.api import routes_chat, routes_export, routes_itinerary, routes_session, routes_system
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.domain.itinerary import Activity, DayDraft
from app.domain.models import Requirements
from app.llm.base import ChatMessage
from app.search.base import SearchResult
from app.storage.database import create_connection
from app.storage.session_store import SessionStore

MARKDOWN = """## 一、行程概览
北京三日游。
## 二、交通建议
往返交通待核实。
## 三、住宿推荐
住宿待核实。
## 四、每日详细行程
原每日正文。
## 五、费用预估
原费用正文。
## 六、避雷与实用贴士
提前预约。[1]
## 七、参考资料
[1]
"""


def requirements():
    return Requirements(
        destination="北京",
        origin="上海",
        duration_days=3,
        budget=2000,
        travelers=2,
        budget_basis="total",
    )


def drafts():
    return [
        DayDraft(
            day=i,
            title=f"第 {i} 天主题",
            activities=[
                Activity(
                    title=f"景点 {i} A",
                    place_name="故宫博物院",
                    city="北京",
                    start_time="09:00",
                    duration_minutes=120,
                    cost=60,
                    cost_basis="per_person",
                    source_indices=[1],
                ),
                Activity(
                    title=f"景点 {i} B",
                    place_name="景山公园",
                    city="北京",
                    start_time="11:00",
                    duration_minutes=60,
                    cost=None,
                ),
            ],
        )
        for i in range(1, 4)
    ]


class FakeLLM:
    name = "fake"

    async def structured(self, messages, schema, **kwargs):
        if schema is CollectResult:
            return CollectResult(extracted=requirements(), reply="需求已收集", ready=True)
        if schema in (PlanOutput, RevisionOutput):
            return schema(markdown=MARKDOWN, days=drafts())
        if schema is PlanDetails:
            return PlanDetails(days=drafts())
        if schema is RefineIntent:
            text = messages[0].content
            return RefineIntent(
                intent="approve" if "本次确认" in text else "modify",
                needs_research="换一家酒店" in text,
            )
        raise AssertionError(schema)

    async def chat(self, messages, **kwargs):
        return MARKDOWN

    async def stream(self, messages: list[ChatMessage], **kwargs):
        yield MARKDOWN[:40]
        yield MARKDOWN[40:]


class FakeSearch:
    name = "fake"

    def __init__(self):
        self.queries = []

    async def search(self, query, **kwargs):
        self.queries.append(query)
        return [
            SearchResult(
                title="官方资料",
                url="https://example.com/info",
                content="门票人均60元",
                provider="fake",
            )
        ]


@pytest_asyncio.fixture
async def environment(tmp_path):
    conn = await create_connection(tmp_path / "test.db")
    settings = Settings(_env_file=None, amap_api_key=None)
    search = FakeSearch()
    graph = build_graph(
        FakeLLM(), search, settings=settings, checkpointer=InMemorySaver(serde=build_serde())
    )
    app = FastAPI()
    app.state.store = SessionStore(conn)
    app.state.graph = graph
    app.dependency_overrides[get_settings] = lambda: settings
    register_exception_handlers(app)
    for router in (routes_session, routes_chat, routes_itinerary, routes_export, routes_system):
        app.include_router(router.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, app.state.store, graph, search
    await conn.close()
