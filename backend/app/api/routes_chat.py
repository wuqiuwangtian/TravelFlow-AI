"""对话接口（规划 §9.2）。

本模块只做三件事：校验会话/分支存在 → 调用服务 → 组装响应。**不含业务逻辑**。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import get_graph, get_store
from app.core.errors import BranchNotFound, SessionNotFound
from app.domain.schemas import ChatRequest, ChatResponse
from app.services.chat_service import ChatService
from app.storage.session_store import SessionStore

router = APIRouter(prefix="/api", tags=["chat"])


async def _resolve_branch(store: SessionStore, session_id: str, branch_id: str | None) -> str:
    """确定本次对话落在哪个分支；未指定时使用主分支。"""
    if branch_id:
        branch = await store.get_branch(branch_id)
        if branch is None or branch.session_id != session_id:
            raise BranchNotFound()
        return branch.id

    branches = await store.list_branches(session_id)
    if not branches:
        created = await store.create_branch(session_id, "主方案", is_primary=True)
        return created.id
    primary = next((b for b in branches if b.is_primary), branches[0])
    return primary.id


@router.post("/chat", response_model=ChatResponse, summary="发送消息")
async def chat(
    payload: ChatRequest,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
) -> ChatResponse:
    """发送一条消息，驱动「收集 → 检索 → 生成 → 修订」流程。"""
    if await store.get_session(payload.session_id) is None:
        raise SessionNotFound()

    branch_id = await _resolve_branch(store, payload.session_id, payload.branch_id)

    service = ChatService(graph=graph, store=store)
    outcome = await service.send(
        session_id=payload.session_id,
        branch_id=branch_id,
        message=payload.message,
        detail_level=payload.detail_level,
    )

    return ChatResponse(
        session_id=payload.session_id,
        branch_id=outcome.branch_id,
        version_id=outcome.version_id,
        status=outcome.status,
        reply=outcome.reply,
        plan=outcome.plan_markdown,
        missing_fields=outcome.missing_fields or [],
    )
