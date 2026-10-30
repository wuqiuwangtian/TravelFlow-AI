"""浏览器验收专用服务：真实接口、编排和持久化，模型与检索用固定替身。"""

import asyncio
import hashlib
import json
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402
from adoption_fixtures import adoption_draft  # noqa: E402
from candidate_fixtures import candidate_draft  # noqa: E402
from conftest import MARKDOWN, FakeLLM, FakeSearch, requirements  # noqa: E402
from expense_fixtures import expense_itinerary  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from geo_fixtures import GeoMaps  # noqa: E402
from linked_fixtures import linked_itinerary  # noqa: E402
from move_fixtures import move_itinerary  # noqa: E402
from ordinary_journey_fixtures import FLOW_MARKER, JourneyMaps, journey_drafts  # noqa: E402
from place_fixtures import place_itinerary  # noqa: E402

from app.agents.checkpoint import open_checkpointer, thread_config  # noqa: E402
from app.agents.graph import build_graph  # noqa: E402
from app.agents.nodes.collector import (  # noqa: E402
    CollectResult,
    DestinationIdea,
    DestinationOptions,
)
from app.agents.nodes.planner import PlanOutput  # noqa: E402
from app.agents.nodes.refiner import OnTripUpdate, RefineIntent  # noqa: E402
from app.agents.state import initial_state  # noqa: E402
from app.api import (  # noqa: E402
    routes_chat,
    routes_export,
    routes_itinerary,
    routes_session,
    routes_studio,  # noqa: E402
    routes_system,
)
from app.core.config import Settings, get_settings  # noqa: E402
from app.core.errors import (
    LLMAuthenticationFailed,  # noqa: E402
    register_exception_handlers,  # noqa: E402
)
from app.domain.enums import PlanStatus  # noqa: E402
from app.domain.itinerary import Booking, BudgetItem, ExpenseCreate, RouteLeg, ScheduledActivity  # noqa: E402
from app.domain.models import Requirements  # noqa: E402
from app.domain.studio_adoption import StudioPlanDraft  # noqa: E402
from app.domain.studio_candidates import CandidateDraft  # noqa: E402
from app.domain.workflow import DayOrder  # noqa: E402
from app.services import itinerary_service, studio_adoption, studio_location, studio_tools  # noqa: E402
from app.services.chat_service import ChatService  # noqa: E402
from app.services.itinerary_service import schedule_day, summarise_day, sync_markdown  # noqa: E402
from app.services.recap_writing import RecapWriting  # noqa: E402
from app.services.replan_service import check_overnight_links  # noqa: E402
from app.services.travel_service import add_expense  # noqa: E402
from app.storage.database import create_connection  # noqa: E402
from app.storage.session_store import SessionStore  # noqa: E402


class BrowserLLM(FakeLLM):
    failed_workflow_cases: set[str] = set()
    empty_city_cases: set[str] = set()

    async def chat(self, messages, **kwargs):
        if "测试失败节点" in messages[0].content:
            raise LLMAuthenticationFailed("验收用模型鉴权失败")
        if "测试慢速节点" in messages[0].content:
            await asyncio.sleep(20)
        if "你是旅行规划工程中的一个生成节点" in messages[0].content:
            fingerprint = hashlib.sha256("".join(m.content for m in messages).encode()).hexdigest()[
                :10
            ]
            return MARKDOWN + "\n节点产物指纹：" + fingerprint
        return await super().chat(messages, **kwargs)

    async def structured(self, messages, schema, **kwargs):
        flow = any(FLOW_MARKER in message.content for message in messages)
        if flow and schema is PlanOutput:
            failure = re.search(r"flow-error-([a-f0-9]{12})", messages[0].content)
            if failure and failure[0] not in self.failed_workflow_cases:
                self.failed_workflow_cases.add(failure[0])
                raise LLMAuthenticationFailed("贯通验收：模型暂时无法鉴权，原条件保留。")
            return PlanOutput(markdown=MARKDOWN, days=journey_drafts())
        if flow and schema is RefineIntent and "23:50" in messages[0].content:
            return RefineIntent(
                intent="modify",
                on_trip=OnTripUpdate(
                    day=1,
                    location_name="北京站",
                    time="23:50",
                    end_time="03:00",
                    end_day_offset=1,
                    end_location_name="示例酒店",
                ),
            )
        if flow and schema is CollectResult:
            text = messages[-1].content
            failure = re.search(r"flow-error-[a-f0-9]{12}", text)
            return CollectResult(
                extracted=Requirements(budget=2000, budget_basis="total", travelers=2)
                if "预算" in text
                else Requirements(
                    origin="上海",
                    duration_days=3,
                    preferences=[FLOW_MARKER, "少走路"] + ([failure[0]] if failure else []),
                ),
                reply="继续补充必要条件",
                wants_destination_ideas=True,
            )
        if schema is CandidateDraft:
            result = candidate_draft()
            if "户外与公园" in messages[0].content:
                result.places[2].cost = 30
            return result
        if schema is StudioPlanDraft:
            if "测试无每日安排" in messages[-1].content:
                result = adoption_draft()
                result.adoptable, result.days, result.reason = (
                    False,
                    [],
                    "正文只有城市推荐，没有每日安排。",
                )
                return result
            return adoption_draft()
        if schema is DayOrder:
            inputs = json.loads(messages[-1].content)
            result, group = [], []
            for activity in inputs["activities"]:
                if activity["fixed"]:
                    result.extend(reversed(group))
                    group = []
                    result.append(activity["id"])
                else:
                    group.append(activity["id"])
            return DayOrder(activity_ids=[*result, *reversed(group)])
        if schema is PlanOutput:
            case = re.search(r"workflow-failure-([a-f0-9]{12})", messages[0].content)
            if case and case[1] not in self.failed_workflow_cases:
                self.failed_workflow_cases.add(case[1])
                raise LLMAuthenticationFailed("验收用鉴权失败")
        if schema is RefineIntent and "这次清空偏好" in messages[0].content:
            return RefineIntent(intent="modify", clear_preferences=True)
        if schema is RecapWriting:
            return RecapWriting(
                title="故宫的旅行片段", text="故宫的一段漫步，成为值得回味的旅行片段。"
            )
        if schema is DestinationOptions:
            case = re.search(r"empty-city-([a-f0-9]{12})", messages[-1].content)
            if case and case[1] not in self.empty_city_cases:
                self.empty_city_cases.add(case[1])
                return DestinationOptions(ideas=[])
            return DestinationOptions(
                ideas=[DestinationIdea(city="北京", reason="博物馆与城市漫步")]
            )
        if schema is CollectResult:
            text = messages[-1].content
            empty_city = re.search(r"空城市推荐验收([a-f0-9]{12})", text)
            if empty_city:
                return CollectResult(
                    extracted=Requirements(
                        origin="上海",
                        duration_days=3,
                        budget=2000,
                        preferences=["empty-city-" + empty_city[1]],
                    ),
                    wants_destination_ideas=True,
                    reply="准备推荐城市",
                )
            case = re.search(r"北京流程中断验收([a-f0-9]{12})", text)
            if case:
                return CollectResult(
                    extracted=requirements().model_copy(
                        update={"preferences": ["workflow-failure-" + case[1]]}
                    ),
                    reply="开始生成验收方案",
                )
            if text == "这次改成徒步":
                return CollectResult(
                    extracted=Requirements(preferences=["徒步"]), reply="按本次徒步偏好继续"
                )
            if text == "这次清空偏好":
                return CollectResult(
                    clear_preferences=True, reply="本次偏好已清空，常用偏好保持原样"
                )
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
    app.state.geo_modes, app.state.geo_calls = {}, {}

    def browser_settings(request: Request):
        for bid, mode in app.state.geo_modes.items():
            if request.url.path.startswith(f"/api/branches/{bid}/studio"):
                return Settings(_env_file=None, amap_api_key=f"isolated-geo-{bid}:{mode}")
        return settings

    app.dependency_overrides[get_settings] = browser_settings

    original_check_day = itinerary_service.check_day

    async def journey_check_day(day, requirements, provider, **kwargs):
        # 仅本场景向正式 check_day 注入离线 provider，不替换编排、匹配或持久化。
        if FLOW_MARKER in requirements.preferences:
            provider = JourneyMaps()
        return await original_check_day(day, requirements, provider, **kwargs)

    itinerary_service.check_day = journey_check_day

    def geo_maps(client, key):
        bid, mode = key.removeprefix("isolated-geo-").split(":", 1)
        return GeoMaps(mode, app.state.geo_calls[bid])

    studio_location.AmapProvider = studio_tools.AmapProvider = studio_adoption.AmapProvider = (
        geo_maps
    )
    # 整理旧路书走独立模型工厂，也必须使用替身，不能尝试读取真实模型配置。
    routes_itinerary.create_llm_provider = lambda settings: BrowserLLM()
    routes_studio.create_llm_provider = lambda settings: BrowserLLM()
    routes_studio.create_search_provider = lambda settings: FakeSearch()
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
            itinerary_service.check_day = original_check_day
            await conn.close()


app = FastAPI(lifespan=lifespan)
register_exception_handlers(app)
for router in (routes_session, routes_chat, routes_export, routes_itinerary, routes_system):
    app.include_router(router.router)
app.include_router(routes_studio.router)


@app.post("/test/geo/{bid}/{mode}")
async def configure_geo(bid: str, mode: str):
    assert mode in {"complete", "ambiguous", "stale", "unavailable", "missing"}
    app.state.geo_modes[bid] = mode
    app.state.geo_calls.setdefault(bid, {"search": [], "route": []})
    return {"configured": True}


@app.get("/test/geo/{bid}")
async def geo_calls(bid: str):
    return app.state.geo_calls[bid]


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
async def place_trip(attention: bool = False, planning_origin: bool = False):
    store = app.state.store
    session = await store.create_session("地点候选验收")
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    itinerary = place_itinerary()
    req = requirements().model_copy(update={"duration_days": 2})
    if planning_origin:
        first, second = itinerary.days
        second.activities[0].booking = Booking(start_time="10:00", duration_minutes=60)
        second.activities.insert(
            0,
            ScheduledActivity(
                id="planned-origin",
                title="规划出发点",
                place_name="示例餐厅",
                city="北京",
                kind="other",
                is_planning_origin=True,
                is_context_anchor=True,
                duration_minutes=0,
                cost=0,
                cost_basis="total",
                cost_source="estimate",
                start_time="09:00",
                candidates=first.activities[0].candidates,
                place_status="ambiguous",
            ),
        )
        second.legs = [RouteLeg(from_id="planned-origin", to_id="dinner", mode="transit")]
        for day in itinerary.days:
            schedule_day(day)
            summarise_day(day, req)
    if attention:
        req.travelers, req.budget_basis = None, None
        first, second = itinerary.days
        first.activities[0].start_time = "23:30"
        first.activities[0].duration_minutes = 120
        first.activities[0].time_is_suggested = False
        first.activities[1].booking = Booking(
            start_time="00:15", start_day_offset=1, duration_minutes=30
        )
        first.legs = [
            RouteLeg(
                from_id="lunch",
                to_id="hotel",
                mode="transit",
                status="planned",
                duration_seconds=3600,
                message="使用建议交通，尚未完成地图核验。",
            )
        ]
        second.activities[0].start_time = "00:30"
        for activity_id, progress, kind in [
            ("already-done", "completed", "attraction"),
            ("already-skipped", "skipped", "attraction"),
            ("free-time", "not_started", "free_time"),
            ("transfer", "not_started", "transport"),
        ]:
            second.activities.append(
                ScheduledActivity(
                    id=activity_id,
                    title=activity_id,
                    kind=kind,
                    progress=progress,
                    cost=0,
                    cost_basis="total",
                )
            )
        second.legs = [
            RouteLeg(
                from_id="dinner",
                to_id="transfer",
                mode="transit",
                status="unavailable",
            )
        ]
        itinerary.budget_items.append(
            BudgetItem(
                id="custom-settled",
                title="已结算未估价项目",
                category="other",
                source="custom",
            )
        )
        add_expense(
            itinerary,
            ExpenseCreate(
                title="已结算未估价项目",
                amount="8.60",
                category="other",
                budget_item_id="custom-settled",
                settles_budget=True,
                request_id="attention-settled-fixture",
            ),
        )
        for day in itinerary.days:
            schedule_day(day)
            summarise_day(day, req)
        check_overnight_links(itinerary)
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


@app.post("/test/career")
async def career_sample(city: str = "泉州"):
    """测试城市独立，避免并行验收的照片分页互相干扰；只在隔离服务中注册。"""
    itinerary = move_itinerary()
    itinerary.days[0].activities[0].city = city
    park = itinerary.days[1].activities[0]
    park.city = city
    assert park.place is not None
    park.place.city = city
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
