"""版本化的每日行程编辑、路线重算及后端地图代理。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError

from app.agents.checkpoint import thread_config
from app.agents.itinerary_output import complete_details, extract_details, prepare_itinerary
from app.core.config import Settings, get_settings
from app.core.deps import get_graph, get_store
from app.core.errors import ConflictError, InvalidRequest
from app.domain.enums import PlanStatus
from app.domain.itinerary import (
    ActivityProgress,
    ActivityUpdate,
    AdjustmentCreate,
    AdjustmentInput,
    Booking,
    BudgetItem,
    DayPlan,
    ExpenseCategory,
    ExpenseCreate,
    ExpenseInput,
    Itinerary,
    ReceiptDraft,
    ReplanInput,
    ScheduledActivity,
    TravelMode,
    TravelRecap,
)
from app.domain.models import Requirements, VersionDetail
from app.domain.schemas import ConnectionTestResponse
from app.llm.factory import create_llm_provider
from app.maps.amap import AmapProvider
from app.maps.assets import map_asset
from app.services.adjustment_service import add_adjustment, edit_adjustment, remove_adjustment
from app.services.branch_lock import branch_guard
from app.services.budget_service import budget_overview, save_budget_item
from app.services.chat_service import ChatService
from app.services.itinerary_service import enrich_itinerary, sync_markdown
from app.services.receipt_service import MAX_RECEIPT_BYTES, normalise_receipt, recognise_receipt
from app.services.replan_service import preserve_replan_scope, record_booking, set_replan_context
from app.services.travel_service import add_expense, edit_expense, remove_expense, set_progress
from app.storage.session_store import SessionStore

router = APIRouter(prefix="/api", tags=["itinerary"])


class RecapInput(TravelRecap):
    source_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.get("/versions/{version_id}/recap")
async def get_recap(version_id: str, store: SessionStore = Depends(get_store)):
    from app.services.recap_service import recap_view

    detail = await _detail(store, version_id)
    return recap_view(_itinerary(detail), detail.requirements or Requirements())


@router.put("/versions/{version_id}/recap", response_model=VersionDetail)
async def save_recap(
    version_id: str,
    payload: RecapInput,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
):
    from app.services.recap_service import recap_view

    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        versions = await store.list_versions(original.version.branch_id)
        detail = await _detail(store, versions[-1].id)
        itinerary = _itinerary(detail)
        view = recap_view(itinerary, detail.requirements or Requirements())
        if view["source_fingerprint"] != payload.source_fingerprint:
            raise ConflictError("行程或账目已变化，请刷新回顾并核对后再保存。")
        fields = itinerary.recap.model_dump() if itinerary.recap else {}
        fields.update(payload.model_dump(exclude_unset=True))
        try:
            recap = TravelRecap.model_validate(fields)
        except ValidationError as exc:
            raise InvalidRequest("请检查照片选择；封面必须选自已选入回顾的照片。") from exc
        from app.services.photo_service import validate_photos

        await validate_photos(store, await _receipt_session(store, detail), recap, itinerary)
        if itinerary.recap == recap:
            return detail
        if detail.version.id != version_id:
            raise ConflictError("当前版本已变化，请刷新后再保存回顾。")
        itinerary.recap = recap
        return await _save_revision(store, graph, detail, "保存旅行回顾", keep_plan_status=True)


@router.post("/versions/{version_id}/travel-photos")
async def upload_travel_photo(
    version_id: str, request: Request, store: SessionStore = Depends(get_store)
):
    from app.services.photo_service import normalise_photo

    detail = await _editable(store, version_id)
    _itinerary(detail)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_RECEIPT_BYTES:
            raise InvalidRequest("请选择4MB以内的旅行照片")
        data.extend(chunk)
    image, width, height, digest = await asyncio.to_thread(normalise_photo, bytes(data))
    row = await store.save_photo(
        await _receipt_session(store, detail), digest, image, width, height
    )
    return {"id": row["id"], "width": width, "height": height}


@router.get("/versions/{version_id}/travel-photos/{photo_id}")
async def get_travel_photo(
    version_id: str, photo_id: str, store: SessionStore = Depends(get_store)
) -> Response:
    detail = await _detail(store, version_id)
    photo = await store.get_photo(await _receipt_session(store, detail), photo_id)
    if photo is None:
        raise InvalidRequest("旅行照片不存在")
    return Response(
        photo["image_data"],
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


class GPSInput(BaseModel):
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)


@router.post("/maps/convert-location")
async def convert_location(payload: GPSInput, settings: Settings = Depends(get_settings)):
    if not settings.amap_api_key:
        raise InvalidRequest("请先配置高德地图 Key，或在高德页面手动选择起点")
    async with httpx.AsyncClient(timeout=settings.amap_timeout_seconds) as client:
        return await AmapProvider(client, settings.amap_api_key).convert_gps(
            payload.longitude, payload.latitude
        )


@router.get("/versions/{version_id}/activities/{activity_id}/photos")
async def place_photos(
    version_id: str,
    activity_id: str,
    store: SessionStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
):
    detail = await _detail(store, version_id)
    activity = next(
        (a for d in _itinerary(detail).days for a in d.activities if a.id == activity_id), None
    )
    if activity is None:
        raise InvalidRequest("地点不存在")
    if not activity.place:
        return {"photos": []}
    if activity.place.photos:
        return {"photos": activity.place.photos}
    if not settings.amap_api_key:
        return {"photos": []}
    async with httpx.AsyncClient(timeout=settings.amap_timeout_seconds) as client:
        place = await AmapProvider(client, settings.amap_api_key).get_place(activity.place.id)
    return {"photos": place.photos}


class BudgetAmount(BaseModel):
    amount: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)


class CustomBudgetInput(BudgetAmount):
    title: str = Field(min_length=1, max_length=100, pattern=r"\S")
    category: ExpenseCategory = "other"
    day: int | None = Field(default=None, ge=1, le=60)
    request_id: str = Field(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")


@router.post("/versions/{version_id}/budget-items", response_model=VersionDetail)
async def create_budget_item(
    version_id: str,
    payload: CustomBudgetInput,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
) -> VersionDetail:
    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        versions = await store.list_versions(original.version.branch_id)
        detail = await _detail(store, versions[-1].id)
        itinerary = _itinerary(detail)
        item = BudgetItem(
            id="custom-" + payload.request_id,
            title=payload.title.strip(),
            category=payload.category,
            day=payload.day,
            planned_cents=int(payload.amount * 100) if payload.amount is not None else None,
        )
        old = next((r for r in itinerary.budget_items if r.id == item.id), None)
        if old:
            if old != item:
                raise ConflictError("同一次预算请求内容已改变，请重新提交")
            return detail
        if detail.version.id != version_id:
            raise ConflictError("当前为历史版本，请先分叉再编辑")
        if item.day is not None and not any(d.day == item.day for d in itinerary.days):
            raise InvalidRequest("日期不在当前行程中")
        save_budget_item(itinerary, item)
        return await _save_revision(
            store, graph, detail, "添加自定义预算项目", keep_plan_status=True
        )


@router.put("/versions/{version_id}/budget-items/{item_id}", response_model=VersionDetail)
async def update_budget_item(
    version_id: str,
    item_id: str,
    payload: BudgetAmount,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
) -> VersionDetail:
    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        detail = await _editable(store, version_id)
        itinerary = _itinerary(detail)
        row = next(
            (
                r
                for r in budget_overview(itinerary, detail.requirements or Requirements()).rows
                if r.id == item_id
            ),
            None,
        )
        if row is None or row.id.startswith("expense-"):
            raise InvalidRequest("预算项目不存在")
        item = BudgetItem(**row.model_dump(include=set(BudgetItem.model_fields)))
        item.planned_cents = int(payload.amount * 100) if payload.amount is not None else None
        save_budget_item(itinerary, item)
        return await _save_revision(store, graph, detail, "修改项目预估费用", keep_plan_status=True)


@router.delete("/versions/{version_id}/budget-items/{item_id}", response_model=VersionDetail)
async def delete_budget_item(
    version_id: str,
    item_id: str,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
) -> VersionDetail:
    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        detail = await _editable(store, version_id)
        itinerary = _itinerary(detail)
        item = next(
            (r for r in itinerary.budget_items if r.id == item_id and r.source == "custom"), None
        )
        if item is None:
            raise InvalidRequest("请选择自定义预算项")
        itinerary.budget_items.remove(item)
        for expense in itinerary.expenses:
            if expense.budget_item_id == item_id:
                expense.budget_item_id, expense.settles_budget = None, False
        return await _save_revision(
            store, graph, detail, "移除自定义预估项目（保留实际消费）", keep_plan_status=True
        )


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
        "pending_journey": None,
        "pending_place": None,
        "pending_linked": None,
        "pending_move": None,
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
            previous=detail.itinerary,
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
        activity.booking = old.booking
        if old.booking and (
            activity.place_name,
            activity.city,
            activity.start_time,
            activity.start_day_offset,
            activity.duration_minutes,
        ) != (old.place_name, old.city, old.start_time, old.start_day_offset, old.duration_minutes):
            raise ConflictError("此站已记录预约；请先更正或清除预约记录，再修改地点和时间。")
        activity.time_is_suggested = (
            old.time_is_suggested
            if (activity.start_time, activity.start_day_offset)
            == (old.start_time, old.start_day_offset)
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
        await _validate_receipts(store, latest, payload)
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
        await _validate_receipts(store, detail, payload, expense_id)
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


@router.post(
    "/versions/{version_id}/expense-adjustments",
    response_model=VersionDetail,
    summary="记录退款或报销（不操作真实资金）",
)
async def create_adjustment(
    version_id: str,
    payload: AdjustmentCreate,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
) -> VersionDetail:
    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        versions = await store.list_versions(original.version.branch_id)
        latest = await _detail(store, versions[-1].id)
        changed = add_adjustment(_itinerary(latest), payload)
        if not changed:
            return latest
        if latest.version.id != version_id:
            raise ConflictError("当前查看的是历史版本，请先分叉为新方案再记录回款")
        return await _save_revision(store, graph, latest, "记录退款或报销", keep_plan_status=True)


@router.put(
    "/versions/{version_id}/expense-adjustments/{adjustment_id}",
    response_model=VersionDetail,
    summary="更正回款或标记到账",
)
async def update_adjustment(
    version_id: str,
    adjustment_id: str,
    payload: AdjustmentInput,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
) -> VersionDetail:
    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        detail = await _editable(store, version_id)
        edit_adjustment(_itinerary(detail), adjustment_id, payload)
        return await _save_revision(store, graph, detail, "更正回款记录", keep_plan_status=True)


@router.delete(
    "/versions/{version_id}/expense-adjustments/{adjustment_id}",
    response_model=VersionDetail,
    summary="移除误记回款（保留历史）",
)
async def delete_adjustment(
    version_id: str,
    adjustment_id: str,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
) -> VersionDetail:
    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        detail = await _editable(store, version_id)
        remove_adjustment(_itinerary(detail), adjustment_id)
        return await _save_revision(store, graph, detail, "移除误记回款", keep_plan_status=True)


class BookingUpdate(BaseModel):
    booking: Booking | None


@router.patch(
    "/versions/{version_id}/days/{number}/activities/{activity_id}/booking",
    response_model=VersionDetail,
    summary="记录或清除用户已有的预约约束",
)
async def update_booking(
    version_id: str,
    number: int,
    activity_id: str,
    payload: BookingUpdate,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
    settings: Settings = Depends(get_settings),
) -> VersionDetail:
    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        detail = await _editable(store, version_id)
        itinerary = _itinerary(detail)
        record_booking(itinerary, number, activity_id, payload.booking)
        detail.itinerary = await enrich_itinerary(
            itinerary, detail.requirements or Requirements(), settings, day_number=number
        )
        summary = "记录已有预约" if payload.booking else "清除预约约束记录（未操作外部预约）"
        return await _save_revision(store, graph, detail, summary, keep_plan_status=True)


@router.post(
    "/versions/{version_id}/days/{number}/replan",
    response_model=VersionDetail,
    summary="从当前地点按剩余时间重排后续，保留已预约项目",
)
async def replan_day(
    version_id: str,
    number: int,
    payload: ReplanInput,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
    settings: Settings = Depends(get_settings),
) -> VersionDetail:
    original = await _detail(store, version_id)
    async with branch_guard(original.version.branch_id):
        detail = await _editable(store, version_id)
        itinerary = _itinerary(detail)
        set_replan_context(itinerary, number, payload)
        preserve_replan_scope(itinerary.model_copy(deep=True), itinerary, number)
        detail.itinerary = await enrich_itinerary(
            itinerary, detail.requirements or Requirements(), settings, day_number=number
        )
        return await _save_revision(
            store, graph, detail, f"从{payload.time}调整第{number}天后续安排"
        )


async def _receipt_session(store: SessionStore, detail: VersionDetail) -> str:
    branch = await store.get_branch(detail.version.branch_id)
    if branch is None:
        raise InvalidRequest("会话不存在")
    return branch.session_id


async def _validate_receipts(
    store: SessionStore, detail: VersionDetail, payload: ExpenseInput, expense_id: str | None = None
) -> None:
    session_id = await _receipt_session(store, detail)
    for receipt_id in payload.receipt_ids:
        if await store.get_receipt(session_id, receipt_id) is None:
            raise InvalidRequest("凭证不存在或不属于这次旅行")
        if expense_id and any(
            e.id == expense_id and receipt_id in e.receipt_ids for e in _itinerary(detail).expenses
        ):
            continue
        duplicate = next(
            (
                e
                for e in _itinerary(detail).expenses
                if e.id != (expense_id or getattr(payload, "request_id", None))
                and receipt_id in e.receipt_ids
            ),
            None,
        )
        if duplicate and not payload.allow_shared_receipts:
            raise ConflictError(
                f"这张凭证已关联「{duplicate.title}」，请更正原记录；确需分项记账时可选择共用凭证。"
            )


@router.post("/versions/{version_id}/receipts", summary="上传本地图片凭证（不自动记账）")
async def upload_receipt(
    version_id: str, request: Request, store: SessionStore = Depends(get_store)
) -> dict:
    detail = await _editable(store, version_id)
    _itinerary(detail)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_RECEIPT_BYTES:
            raise InvalidRequest("请选择4MB以内的凭证图片")
        data.extend(chunk)
    original = bytes(data)
    preview, mime, width, height, digest = await asyncio.to_thread(normalise_receipt, original)
    row = await store.save_receipt(
        await _receipt_session(store, detail), digest, original, preview, mime, width, height
    )
    return {
        "id": row["id"],
        "width": row["width"],
        "height": row["height"],
        "mime": row["original_mime"],
    }


@router.get("/versions/{version_id}/receipts/{receipt_id}", summary="查看凭证预览或下载原图")
async def get_receipt(
    version_id: str,
    receipt_id: str,
    original: bool = False,
    store: SessionStore = Depends(get_store),
) -> Response:
    detail = await _detail(store, version_id)
    row = await store.get_receipt(await _receipt_session(store, detail), receipt_id)
    if row is None:
        raise InvalidRequest("凭证不存在或不属于这次旅行")
    mime = row["original_mime"] if original else "image/jpeg"
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[mime]
    return Response(
        row["original_data"] if original else row["preview_data"],
        media_type=mime,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Content-Disposition": (
                f"{'attachment' if original else 'inline'}; filename=receipt-{row['id']}.{ext}"
            ),
        },
    )


@router.post(
    "/versions/{version_id}/receipts/{receipt_id}/recognise",
    response_model=ReceiptDraft,
    summary="将凭证发给当前模型识别为待核对草稿",
)
async def recognise(
    version_id: str,
    receipt_id: str,
    store: SessionStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> ReceiptDraft:
    detail = await _editable(store, version_id)
    row = await store.get_receipt(await _receipt_session(store, detail), receipt_id)
    if row is None:
        raise InvalidRequest("凭证不存在或不属于这次旅行")
    return await recognise_receipt(create_llm_provider(settings), row["preview_data"])
