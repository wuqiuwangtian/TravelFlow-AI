"""浏览器验收专用服务：真实接口、编排和持久化，模型与检索用固定替身。"""

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402
from conftest import MARKDOWN, FakeLLM, FakeSearch, requirements  # noqa: E402
from expense_fixtures import expense_itinerary  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from linked_fixtures import linked_itinerary  # noqa: E402
from move_fixtures import move_itinerary  # noqa: E402
from place_fixtures import place_itinerary  # noqa: E402

from app.agents.checkpoint import open_checkpointer, thread_config  # noqa: E402
from app.agents.graph import build_graph  # noqa: E402
from app.agents.nodes.collector import (  # noqa: E402
    CollectResult,
    DestinationIdea,
    DestinationOptions,
)
from app.agents.state import initial_state  # noqa: E402
from app.api import routes_chat, routes_export, routes_itinerary, routes_session, routes_system  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.core.errors import register_exception_handlers  # noqa: E402
from app.domain.enums import PlanStatus  # noqa: E402
from app.domain.models import Requirements  # noqa: E402
from app.services.chat_service import ChatService  # noqa: E402
from app.services.itinerary_service import sync_markdown  # noqa: E402
from app.services.recap_writing import RecapWriting  # noqa: E402
from app.storage.database import create_connection  # noqa: E402
from app.storage.session_store import SessionStore  # noqa: E402


class BrowserLLM(FakeLLM):
    async def structured(self, messages, schema, **kwargs):
        if schema is RecapWriting:
            return RecapWriting(
                title="故宫的旅行片段", text="故宫的一段漫步，成为值得回味的旅行片段。"
            )
        if schema is DestinationOptions:
            return DestinationOptions(
                ideas=[DestinationIdea(city="北京", reason="博物馆与城市漫步")]
            )
        if schema is CollectResult:
            text = messages[-1].content
            if "北京" in text:
                return CollectResult(extracted=requirements(), reply="开始规划")
            return CollectResult(
                extracted=Requirements(budget=2000)
                if "预算" in text
                else Requirements(origin="上海", duration_days=3, preferences=["少走路"]),
                wants_destination_ideas="推荐" in text,
                reply="还需要推荐吗？",  # 由真实 collector 覆盖；防止补预算后重复追问。
            )
        return await super().structured(messages, schema, **kwargs)


@asynccontextmanager
async def lifespan(app):
    # 每次启动独立临时目录；不加载 .env，不接触个人数据库，退出自动清理。
    settings = Settings(_env_file=None, llm_api_key=None, search_api_key=None, amap_api_key=None)
    app.dependency_overrides[get_settings] = lambda: settings
    # 整理旧路书走独立模型工厂，也必须使用替身，不能尝试读取真实模型配置。
    routes_itinerary.create_llm_provider = lambda settings: BrowserLLM()
    with TemporaryDirectory(prefix="travelflow-browser-") as folder:
        db = Path(folder) / "acceptance.db"
        conn = await create_connection(db)
        try:
            async with open_checkpointer(db) as saver:
                app.state.store = SessionStore(conn)
                app.state.graph = build_graph(
                    BrowserLLM(), FakeSearch(), settings=settings, checkpointer=saver
                )
                yield
        finally:
            await conn.close()


app = FastAPI(lifespan=lifespan)
register_exception_handlers(app)
for router in (routes_session, routes_chat, routes_export, routes_itinerary, routes_system):
    app.include_router(router.router)


@app.post("/test/legacy")
async def legacy():
    """只挂在本测试服务上，构造没有结构化行程的旧文字路书。"""
    store = app.state.store
    session = await store.create_session("旧路书验收")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    values = dict(initial_state())
    values.update(requirements=requirements(), plan_markdown=MARKDOWN, status=PlanStatus.REVIEWING)
    config = await app.state.graph.aupdate_state(
        thread_config(branch.id), values, as_node="planner"
    )
    await ChatService(graph=app.state.graph, store=store).record_version(branch.id, values, config)
    return {"session_id": session.id, "branch_id": branch.id}


@app.post("/test/places")
async def place_trip():
    store = app.state.store
    session = await store.create_session("地点候选验收")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    itinerary = place_itinerary()
    req = requirements().model_copy(update={"duration_days": 2})
    values = {
        **initial_state(),
        "requirements": req,
        "itinerary": itinerary,
        "plan_markdown": sync_markdown(MARKDOWN.replace("[1]", ""), itinerary, req),
        "status": PlanStatus.REVIEWING,
    }
    config = await app.state.graph.aupdate_state(
        thread_config(branch.id), values, as_node="planner"
    )
    await ChatService(graph=app.state.graph, store=store).record_version(branch.id, values, config)
    return {"session_id": session.id, "branch_id": branch.id}


@app.post("/test/expenses")
async def expense_trip():
    store = app.state.store
    session = await store.create_session("同日消费候选验收")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    itinerary = expense_itinerary()
    req = requirements().model_copy(update={"duration_days": 2})
    values = {
        **initial_state(),
        "requirements": req,
        "itinerary": itinerary,
        "plan_markdown": sync_markdown(MARKDOWN.replace("[1]", ""), itinerary, req),
        "status": PlanStatus.REVIEWING,
    }
    config = await app.state.graph.aupdate_state(
        thread_config(branch.id), values, as_node="planner"
    )
    await ChatService(graph=app.state.graph, store=store).record_version(branch.id, values, config)
    return {"session_id": session.id, "branch_id": branch.id}


@app.post("/test/linked")
async def linked_trip(early_booking: bool = False, optional_stop: bool = False):
    return await seed_linked_trip(
        linked_itinerary(early_booking=early_booking, optional_stop=optional_stop)
    )


@app.post("/test/moves")
async def move_trip(ambiguous: bool = False, booked: bool = False, payment_unknown: bool = False):
    itinerary = move_itinerary(ambiguous=ambiguous, booked=booked)
    if payment_unknown:
        itinerary.expenses[0].day = None
    return await seed_linked_trip(itinerary)


async def seed_linked_trip(itinerary):
    store = app.state.store
    session = await store.create_session("两天联动验收")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    req = requirements().model_copy(update={"duration_days": 3})
    values = {
        **initial_state(),
        "requirements": req,
        "itinerary": itinerary,
        "plan_markdown": sync_markdown(MARKDOWN.replace("[1]", ""), itinerary, req),
        "status": PlanStatus.REVIEWING,
    }
    config = await app.state.graph.aupdate_state(
        thread_config(branch.id), values, as_node="planner"
    )
    await ChatService(graph=app.state.graph, store=store).record_version(branch.id, values, config)
    return {"session_id": session.id, "branch_id": branch.id}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8137)
