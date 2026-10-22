"""版本化的每日行程编辑、路线重算及后端地图代理。"""

from __future__ import annotations

from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel

from app.agents.checkpoint import thread_config
from app.agents.itinerary_output import complete_details, extract_details, prepare_itinerary
from app.core.config import Settings, get_settings
from app.core.deps import get_graph, get_store
from app.core.errors import ConflictError, InvalidRequest
from app.domain.enums import PlanStatus
from app.domain.itinerary import (
    ActivityProgress,
    ActivityUpdate,
    DayPlan,
    ExpenseCreate,
    ExpenseInput,
    Itinerary,
    ScheduledActivity,
    TravelMode,
)
from app.domain.models import Requirements, VersionDetail
from app.domain.schemas import ConnectionTestResponse
from app.llm.factory import create_llm_provider
from app.maps.amap import AmapProvider
from app.maps.assets import map_asset
from app.services.branch_lock import branch_guard
from app.services.chat_service import ChatService
from app.services.itinerary_service import enrich_itinerary, sync_markdown
from app.services.travel_service import add_expense, edit_expense, remove_expense, set_progress
from app.storage.session_store import SessionStore

router = APIRouter(prefix="/api", tags=["itinerary"])


async def _detail(store: SessionStore, version_id: str) -> VersionDetail:
    detail = await store.get_version_detail(version_id)
    if detail is None:
        raise InvalidRequest("版本不存在")
    return detail


async def _editable(store: SessionStore, version_id: str) -> VersionDetail:
    detail = await _detail(store, version_id)
    versions = await store.list_versions(detail.version.branch_id)
    if not versions or versions[-1].id != version_id:
        raise ConflictError("当前查看的是历史版本，请先分叉为新方案再编辑")
    return detail


def _day(detail: VersionDetail, number: int) -> DayPlan:
    if detail.itinerary:
        for day in detail.itinerary.days:
            if day.day == number:
                return day
    raise InvalidRequest("未找到当天行程，请先整理每日行程")


async def _save_revision(
    store: SessionStore,
    graph,
    detail: VersionDetail,
    summary: str,
    *,
    keep_plan_status: bool = False,
) -> VersionDetail:
    """同时更新图状态和版本快照，下一轮对话接着修改后的行程继续。"""
    branch_id = detail.version.branch_id
    config = thread_config(branch_id)
    snapshot = await graph.aget_state(config)
    values = dict(snapshot.values or {})
    requirements = detail.requirements or values.get("requirements") or Requirements()
    detail.requirements = requirements
    if detail.itinerary:
        detail.markdown = sync_markdown(detail.markdown, detail.itinerary, requirements)
    patch = {
        "plan_markdown": detail.markdown,
        "itinerary": detail.itinerary,
        "requirements": requirements,
        "todos": detail.todos,
        "revision": int(values.get("revision", 0)) + 1,
        "status": values.get("status", PlanStatus.REVIEWING)
        if keep_plan_status
        else PlanStatus.REVIEWING,
        "updated_at": datetime.now(),
    }
    updated = await graph.aupdate_state(config, patch, as_node="refiner")
    values.update(patch)
    # 旧库若无 research checkpoint，仍保留版本已有的引用。
    version_id = await ChatService(graph=graph, store=store).record_version(
        branch_id,
        values,
        updated,
        trigger="itinerary_edit",
        summary=summary,
    )
    if version_id is None:
        raise InvalidRequest("路书内容为空，无法保存")
    await store.save_plan(
        version_id,
        markdown=detail.markdown,
        itinerary=detail.itinerary,
        requirements=requirements,
        citations=detail.citations,
        todos=detail.todos,
        searched_at=detail.searched_at,
    )
    branch = await store.get_branch(branch_id)
    if branch:
        await store.touch_session(branch.session_id)
    return await _detail(store, version_id)


@router.get("/maps/places", summary="搜索高德地点候选")
async def places(
    keyword: str = Query(min_length=1, max_length=80),
    city: str = Query(min_length=1, max_length=50),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not settings.amap_api_key:
        raise InvalidRequest("请先在设置中填写高德 Web 服务 Key")
    async with httpx.AsyncClient(timeout=settings.amap_timeout_seconds) as client:
        results = await AmapProvider(client, settings.amap_api_key).search_places(keyword, city)
    return {"items": results}


@router.post("/system/test-map", response_model=ConnectionTestResponse, summary="测试高德连接")
async def test_map(settings: Settings = Depends(get_settings)) -> ConnectionTestResponse:
    if not settings.amap_api_key:
        return ConnectionTestResponse(
            ok=False, provider="amap", message="请先保存高德 Web 服务 Key"
        )
    try:
        async with httpx.AsyncClient(timeout=settings.amap_timeout_seconds) as client:
            items = await AmapProvider(client, settings.amap_api_key).search_places(
                "故宫博物院", "北京"
            )
        return ConnectionTestResponse(
            ok=bool(items),
            provider="amap",
            message="地点查询正常" if items else "未查到地点，请检查服务权限",
        )
    except Exception:
        return ConnectionTestResponse(
            ok=False, provider="amap", message="连接失败，请检查 Key、服务权限、额度或网络"
        )


@router.post(
    "/versions/{version_id}/itinerary", response_model=VersionDetail, summary="整理每日行程"
)
async def organise(
    version_id: str,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
    settings: Settings = Depends(get_settings),
) -> VersionDetail:
    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        detail = await _editable(store, version_id)
        snapshot = await graph.aget_state(thread_config(detail.version.branch_id))
        requirements = (
            detail.requirements or (snapshot.values or {}).get("requirements") or Requirements()
        )
        llm = create_llm_provider(settings)
        details = await extract_details(llm, detail.markdown, requirements)
        research = (snapshot.values or {}).get("research")
        details = await complete_details(
            llm, details, requirements, research.numbered_sources() if research else detail.markdown
        )
        detail.itinerary, detail.markdown = await prepare_itinerary(
            details,
            detail.markdown,
            requirements,
            settings,
            len(detail.citations),
        )
        if detail.itinerary is None:
            raise InvalidRequest("未识别到每日行程，请先生成包含每日安排的路书")
        detail.requirements, detail.todos = requirements, details.todos
        return await _save_revision(store, graph, detail, "已整理每日行程")


@router.put(
    "/versions/{version_id}/days/{number}/activities/{activity_id}",
    response_model=VersionDetail,
    summary="替换活动并重算当天路线及费用",
)
async def update_activity(
    version_id: str,
    number: int,
    activity_id: str,
    payload: ActivityUpdate,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
    settings: Settings = Depends(get_settings),
) -> VersionDetail:
    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        detail = await _editable(store, version_id)
        day = _day(detail, number)
        index = next((i for i, a in enumerate(day.activities) if a.id == activity_id), None)
        if index is None:
            raise InvalidRequest("活动不存在")
        old = day.activities[index]
        activity = ScheduledActivity(id=activity_id, **payload.activity.model_dump())
        activity.progress, activity.progress_updated_at = old.progress, old.progress_updated_at
        activity.time_is_suggested = (
            old.time_is_suggested
            if activity.start_time == old.start_time
            else activity.start_time is None
        )
        # 同地点编辑时复用已确认坐标；替换地点时只能使用高德返回的坐标。
        if payload.place_id and old.place and old.place.id == payload.place_id:
            activity.place, activity.place_status = old.place, "matched"
            activity.place_name = old.place.name
            activity.city = old.place.city or activity.city
        elif payload.place_id:
            if not settings.amap_api_key:
                raise InvalidRequest("请先配置高德 Key 后再确认地点")
            async with httpx.AsyncClient(timeout=settings.amap_timeout_seconds) as client:
                activity.place = await AmapProvider(client, settings.amap_api_key).get_place(
                    payload.place_id
                )
            activity.place_status = "matched"
            activity.place_name = activity.place.name
            activity.city = activity.place.city or activity.city
        elif (activity.place_name, activity.city) == (old.place_name, old.city):
            activity.place, activity.place_status = old.place, old.place_status
        # 用户编辑的费用保留为手动估算，不冒用原活动的引用。
        activity.source_indices = [
            i for i in activity.source_indices if any(c.index == i for c in detail.citations)
        ]
        if (
            activity.place_name != old.place_name
            or activity.cost != old.cost
            or activity.cost_basis != old.cost_basis
        ):
            activity.source_indices = []
            if activity.cost is not None:
                activity.notes = (activity.notes + "（费用为手动估算）")[:2000]
                activity.cost_source = "manual"
        day.activities[index] = activity
        assert detail.itinerary is not None
        detail.itinerary = await enrich_itinerary(
            detail.itinerary, detail.requirements or Requirements(), settings, day_number=number
        )
        return await _save_revision(store, graph, detail, f"更新第 {number} 天：{activity.title}")


class RouteUpdate(BaseModel):
    travel_mode: TravelMode


@router.post(
    "/versions/{version_id}/days/{number}/routes",
    response_model=VersionDetail,
    summary="校验或切换当天交通方式",
)
async def recalculate(
    version_id: str,
    number: int,
    payload: RouteUpdate,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
    settings: Settings = Depends(get_settings),
) -> VersionDetail:
    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        detail = await _editable(store, version_id)
        _day(detail, number).travel_mode = payload.travel_mode
        assert detail.itinerary is not None
        detail.itinerary = await enrich_itinerary(
            detail.itinerary, detail.requirements or Requirements(), settings, day_number=number
        )
        return await _save_revision(store, graph, detail, f"重新校验第 {number} 天路线")


@router.get("/versions/{version_id}/days/{number}/map", summary="每日高德地图图片")
async def day_map(
    version_id: str,
    number: int,
    store: SessionStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> Response:
    if not settings.amap_api_key:
        raise InvalidRequest("请先配置高德 Key")
    day = _day(await _detail(store, version_id), number)
    data, content_type = await map_asset(version_id, day, settings)
    return Response(
        data, media_type=content_type, headers={"Cache-Control": "private, max-age=300"}
    )


def _itinerary(detail: VersionDetail) -> Itinerary:
    if detail.itinerary is None:
        raise InvalidRequest("请先生成或整理每日行程")
    return detail.itinerary


class ProgressUpdate(BaseModel):
    progress: ActivityProgress


@router.patch(
    "/versions/{version_id}/days/{number}/activities/{activity_id}/progress",
    response_model=VersionDetail,
    summary="记录旅中活动状态",
)
async def update_progress(
    version_id: str,
    number: int,
    activity_id: str,
    payload: ProgressUpdate,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
) -> VersionDetail:
    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        detail = await _editable(store, version_id)
        set_progress(_itinerary(detail), number, activity_id, payload.progress)
        return await _save_revision(
            store, graph, detail, f"记录第 {number} 天活动状态", keep_plan_status=True
        )


@router.post(
    "/versions/{version_id}/expenses", response_model=VersionDetail, summary="记录实际消费"
)
async def create_expense(
    version_id: str,
    payload: ExpenseCreate,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
) -> VersionDetail:
    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        versions = await store.list_versions(original.version.branch_id)
        latest = await _detail(store, versions[-1].id)
        changed = add_expense(_itinerary(latest), payload)
        if not changed:
            return latest  # 网络重试可使用原版本和同一 request_id，不重复记账。
        if latest.version.id != version_id:
            raise ConflictError("当前查看的是历史版本，请先分叉为新方案再记账")
        return await _save_revision(
            store, graph, latest, f"记账：{payload.title}", keep_plan_status=True
        )


@router.put(
    "/versions/{version_id}/expenses/{expense_id}",
    response_model=VersionDetail,
    summary="更正实际消费",
)
async def update_expense(
    version_id: str,
    expense_id: str,
    payload: ExpenseInput,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
) -> VersionDetail:
    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        detail = await _editable(store, version_id)
        edit_expense(_itinerary(detail), expense_id, payload)
        return await _save_revision(
            store, graph, detail, f"更正消费：{payload.title}", keep_plan_status=True
        )


@router.delete(
    "/versions/{version_id}/expenses/{expense_id}",
    response_model=VersionDetail,
    summary="移除误记消费（保留历史版本）",
)
async def delete_expense(
    version_id: str,
    expense_id: str,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
) -> VersionDetail:
    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        detail = await _editable(store, version_id)
        remove_expense(_itinerary(detail), expense_id)
        return await _save_revision(store, graph, detail, "移除误记消费", keep_plan_status=True)
