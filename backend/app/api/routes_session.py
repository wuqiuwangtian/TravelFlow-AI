"""会话、分支与版本接口（规划 §9.2）。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request

from app.agents.checkpoint import thread_config
from app.agents.state import initial_state
from app.core.deps import get_graph, get_store
from app.core.errors import BranchNotFound, ConfigError, InvalidRequest, SessionNotFound
from app.domain.career import TravelCareer
from app.domain.clarification import clarification_prompt
from app.domain.enums import PlanStatus
from app.domain.models import ResearchBundle
from app.domain.schemas import (
    BranchListResponse,
    ChatHistoryResponse,
    ChatMessageItem,
    CreateBranchRequest,
    CreateSessionRequest,
    SessionDetailResponse,
    SessionListResponse,
    UpdateSessionRequest,
    VersionListResponse,
)
from app.search.base import SearchResult
from app.services.branch_lock import branch_guard
from app.services.chat_service import ChatService
from app.storage.session_store import SessionStore

router = APIRouter(prefix="/api", tags=["session"])


@router.get("/travel-career", response_model=TravelCareer, summary="基于实际记录的旅行生涯")
async def get_travel_career(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    city: str | None = Query(None, max_length=100),
    store: SessionStore = Depends(get_store),
) -> TravelCareer:
    from app.services.career_service import travel_career

    return await travel_career(
        store, page=page, page_size=page_size, city=(city or "").strip() or None
    )


@router.get("/recap-archive", summary="已结束方案的旅行回顾")
async def recap_archive(
    q: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    store: SessionStore = Depends(get_store),
):
    from app.domain.models import Requirements
    from app.services.recap_service import recap_view

    rows, total = await store.list_recap_archive(
        query=q.strip() if q else None, limit=page_size, offset=(page - 1) * page_size
    )
    items = []
    for row in rows:
        detail = await store.get_version_detail(row["version_id"])
        if detail is None or detail.itinerary is None:
            continue
        view = recap_view(detail.itinerary, detail.requirements or Requirements())
        items.append(
            {
                **row,
                "session_archived": bool(row["session_archived"]),
                "ended_at": view["ended_at"],
                "title": view["title"],
                "destination": view["destination"],
                "completed_count": sum(len(d["completed"]) for d in view["days"]),
                "unconfirmed_count": sum(len(d["unconfirmed"]) for d in view["days"]),
                "net_expense_cents": view["ledger"]["net_expense_cents"],
                "expense_count": view["expense_count"],
                "unsettled_count": view["unsettled_count"],
                "cover_photo_id": view["cover_photo_id"],
                "stale": view["stale"],
            }
        )
    return {"items": items, "total": total}


async def _require_session(store: SessionStore, session_id: str):
    session = await store.get_session(session_id)
    if session is None:
        raise SessionNotFound()
    return session


# ============================================================ 会话
@router.get("/sessions", response_model=SessionListResponse, summary="会话列表")
async def list_sessions(
    archived: bool = False,
    q: str | None = Query(None, description="按标题搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    store: SessionStore = Depends(get_store),
) -> SessionListResponse:
    items = await store.list_sessions(
        archived=archived, query=q, limit=page_size, offset=(page - 1) * page_size
    )
    # total 必须是「总数」而不是本页数量，否则前端分页会算错
    total = await store.count_sessions(archived=archived, query=q)
    return SessionListResponse(items=items, total=total)


@router.post("/sessions", response_model=SessionDetailResponse, summary="新建会话")
async def create_session(
    payload: CreateSessionRequest,
    store: SessionStore = Depends(get_store),
) -> SessionDetailResponse:
    """新建会话，并自动创建一个主分支。"""
    session = await store.create_session(payload.title)
    branch = await store.create_branch(session.id, "主方案", is_primary=True)
    refreshed = await store.get_session(session.id)
    return SessionDetailResponse(
        session=refreshed or session, branches=[branch], versions={branch.id: []}
    )


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse, summary="会话详情")
async def get_session(
    session_id: str,
    store: SessionStore = Depends(get_store),
) -> SessionDetailResponse:
    session = await _require_session(store, session_id)
    branches = await store.list_branches(session_id)
    versions = {branch.id: await store.list_versions(branch.id) for branch in branches}
    return SessionDetailResponse(session=session, branches=branches, versions=versions)


@router.patch("/sessions/{session_id}", response_model=SessionDetailResponse, summary="更新会话")
async def update_session(
    session_id: str,
    payload: UpdateSessionRequest,
    store: SessionStore = Depends(get_store),
) -> SessionDetailResponse:
    await _require_session(store, session_id)

    if payload.title is not None:
        await store.rename_session(session_id, payload.title)
    if payload.archived is not None:
        await store.set_archived(session_id, payload.archived)

    return await get_session(session_id, store)


@router.delete("/sessions/{session_id}", summary="删除会话")
async def delete_session(
    session_id: str,
    store: SessionStore = Depends(get_store),
) -> dict[str, bool]:
    await _require_session(store, session_id)
    return {"deleted": await store.delete_session(session_id)}


# ============================================================ 分支
@router.get(
    "/sessions/{session_id}/branches", response_model=BranchListResponse, summary="分支列表"
)
async def list_branches(
    session_id: str,
    store: SessionStore = Depends(get_store),
) -> BranchListResponse:
    await _require_session(store, session_id)
    return BranchListResponse(items=await store.list_branches(session_id))


@router.post(
    "/sessions/{session_id}/branches", response_model=BranchListResponse, summary="新建分支"
)
async def create_branch(
    session_id: str,
    payload: CreateBranchRequest,
    request: Request,
    store: SessionStore = Depends(get_store),
) -> BranchListResponse:
    """从某个版本（或某个分支的最新状态）分叉出一条新分支。"""
    await _require_session(store, session_id)

    parent_id = payload.parent_branch_id
    if payload.fork_version_id and not parent_id:
        version = await store.get_version(payload.fork_version_id)
        if version is None:
            raise InvalidRequest("指定的版本不存在")
        parent_id = version.branch_id

    parent = await store.get_branch(parent_id) if parent_id else None
    if parent_id and (parent is None or parent.session_id != session_id):
        raise InvalidRequest("源分支不属于当前会话")
    source = (
        await store.get_version_detail(payload.fork_version_id) if payload.fork_version_id else None
    )
    if source and source.version.branch_id != parent_id:
        raise InvalidRequest("源版本不属于指定分支")
    graph = getattr(request.app.state, "graph", None)
    if parent_id and graph is None:
        raise ConfigError("请先完成模型与检索配置，再从历史方案继续规划")
    async with branch_guard(parent_id or session_id):
        values = {}
        if parent_id:
            assert graph is not None
            source_config = thread_config(parent_id)
            if source and source.version.checkpoint_id:
                source_config = thread_config(parent_id, checkpoint_id=source.version.checkpoint_id)
            if source is None or source.version.checkpoint_id:
                snapshot = await graph.aget_state(source_config)
                values = dict(snapshot.values or {})
            # 兼容只有业务版本、没有 checkpoint 的旧记录。
            if source:
                values = values or dict(initial_state())
                values.update(
                    plan_markdown=source.markdown,
                    itinerary=source.itinerary,
                    todos=source.todos,
                    status=PlanStatus.REVIEWING,
                )
                if source.requirements:
                    values["requirements"] = source.requirements
                if not values.get("research") and source.citations:
                    values["research"] = ResearchBundle(
                        transport=[
                            SearchResult(title=c.title, url=c.url, content="", provider=c.provider)
                            for c in source.citations
                        ],
                        searched_at=source.searched_at or datetime.now(),
                    )
        branch = await store.create_branch(
            session_id,
            payload.title,
            parent_branch_id=parent_id,
            fork_version_id=payload.fork_version_id,
        )
        if values:
            assert graph is not None
            if values.get("itinerary") is not None:
                values["itinerary"] = values["itinerary"].model_copy(
                    deep=True, update={"ended_at": None}
                )
            values["pending_journey"] = None
            values["pending_place"] = None
            values["pending_linked"] = None
            values["pending_move"] = None
            values["chat_request"] = None
            values["completed_chat"] = None
            config = await graph.aupdate_state(thread_config(branch.id), values, as_node="refiner")
            vid = await ChatService(graph=graph, store=store).record_version(
                branch.id,
                values,
                config,
                trigger="fork",
                summary="从原方案分叉",
            )
            if source and vid:
                await store.save_plan(
                    vid,
                    markdown=source.markdown,
                    itinerary=values.get("itinerary"),
                    requirements=source.requirements,
                    citations=source.citations,
                    todos=source.todos,
                    searched_at=source.searched_at,
                )
    return BranchListResponse(items=await store.list_branches(session_id))


@router.post(
    "/branches/{branch_id}/primary", response_model=BranchListResponse, summary="设为主分支"
)
async def set_primary_branch(
    branch_id: str,
    store: SessionStore = Depends(get_store),
) -> BranchListResponse:
    branch = await store.get_branch(branch_id)
    if branch is None:
        raise BranchNotFound()
    await store.set_primary_branch(branch.session_id, branch_id)
    return BranchListResponse(items=await store.list_branches(branch.session_id))


@router.delete("/branches/{branch_id}", summary="删除分支")
async def delete_branch(
    branch_id: str,
    store: SessionStore = Depends(get_store),
) -> dict[str, bool]:
    branch = await store.get_branch(branch_id)
    if branch is None:
        raise BranchNotFound()
    if branch.is_primary:
        raise InvalidRequest("主分支不可删除")
    return {"deleted": await store.delete_branch(branch_id)}


# ============================================================ 版本
@router.get(
    "/branches/{branch_id}/versions", response_model=VersionListResponse, summary="版本列表"
)
async def list_versions(
    branch_id: str,
    store: SessionStore = Depends(get_store),
) -> VersionListResponse:
    if await store.get_branch(branch_id) is None:
        raise BranchNotFound()
    return VersionListResponse(items=await store.list_versions(branch_id))


@router.get("/versions/{version_id}", summary="版本详情")
async def get_version(
    version_id: str,
    store: SessionStore = Depends(get_store),
) -> dict:
    detail = await store.get_version_detail(version_id)
    if detail is None:
        raise InvalidRequest("版本不存在")
    return detail.model_dump(mode="json")


@router.get(
    "/branches/{branch_id}/messages",
    response_model=ChatHistoryResponse,
    summary="对话历史",
)
async def get_branch_messages(
    branch_id: str,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
) -> ChatHistoryResponse:
    """读取该分支的完整对话历史。

    历史存在 LangGraph 的 checkpoint 中（``messages`` 字段）。
    没有这个接口，用户刷新页面就会看到「空对话」——
    数据明明存着，却像丢了一样。
    """
    if await store.get_branch(branch_id) is None:
        raise BranchNotFound()

    snapshot = await graph.aget_state(thread_config(branch_id))
    raw_messages = (snapshot.values or {}).get("messages", []) if snapshot else []

    items: list[ChatMessageItem] = []
    for message in raw_messages:
        # 只回传纯文本消息（工具调用等中间态对用户没有意义）
        content = getattr(message, "content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        role = "user" if getattr(message, "type", "") == "human" else "assistant"
        items.append(ChatMessageItem(role=role, content=content))

    return ChatHistoryResponse(
        messages=items,
        clarification=clarification_prompt(
            (snapshot.values or {}).get("pending_journey")
            or (snapshot.values or {}).get("pending_place")
            or (snapshot.values or {}).get("pending_linked")
            or (snapshot.values or {}).get("pending_move")
        ),
    )
