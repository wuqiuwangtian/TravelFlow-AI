"""对话接口（规划 §9.2）。

本模块只做三件事：校验会话/分支存在 → 调用服务 → 组装响应。**不含业务逻辑**。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict, is_dataclass

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.deps import get_graph, get_store
from app.core.errors import AppError, BranchNotFound, SessionNotFound
from app.core.logging import get_logger
from app.domain.schemas import ChatRequest, ChatResponse
from app.services.chat_service import ChatService
from app.storage.session_store import SessionStore

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

#: 未命名会话的默认标题。**只有**标题仍是其中之一时才自动命名，
#: 以免覆盖用户自己起的名字。
DEFAULT_TITLES = {"新的旅行", "新的旅程", "新会话", "未命名", "新对话"}

#: 自动标题的最大长度
AUTO_TITLE_MAX = 20


def _auto_title(message: str) -> str:
    """用用户的第一句话生成会话标题。

    刻意**不调用 LLM**：为了起个名字多花一次调用不划算，
    截取用户原话反而更好认（一眼就知道当时说了什么）。
    """
    text = " ".join(message.split())  # 折叠换行与多余空白
    if not text:
        return ""
    if len(text) <= AUTO_TITLE_MAX:
        return text
    return text[:AUTO_TITLE_MAX] + "…"


async def _autoname_if_untitled(store: SessionStore, session_id: str, message: str) -> None:
    """首轮对话后给会话起个像样的名字。

    会话列表里一排「新的旅行」完全无法分辨，用户又找不到改名的入口，
    所以这里自动命名 + 前端提供手动重命名。
    """
    session = await store.get_session(session_id)
    if session is None or session.title not in DEFAULT_TITLES:
        return
    title = _auto_title(message)
    if title:
        await store.rename_session(session_id, title)


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

    # 首轮对话后自动命名（用户自己改过名字则不动）
    await _autoname_if_untitled(store, payload.session_id, payload.message)

    return ChatResponse(
        session_id=payload.session_id,
        branch_id=outcome.branch_id,
        version_id=outcome.version_id,
        status=outcome.status,
        reply=outcome.reply,
        plan=outcome.plan_markdown,
        missing_fields=outcome.missing_fields or [],
    )


def _sse(event: str, data: object) -> str:
    """按 SSE 规范拼一条消息。"""
    if is_dataclass(data) and not isinstance(data, type):
        data = asdict(data)
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("/chat/stream", summary="发送消息（流式）")
async def chat_stream(
    payload: ChatRequest,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
) -> StreamingResponse:
    """与 ``POST /api/chat`` 等价，但把过程实时推给前端。

    为什么需要：一次生成通常要 30~40 秒（收集 + 三路检索 + 撰写），
    一次性返回会让界面一直干等。这里改为：

    - ``event: custom`` —— 节点推送的进度与增量文本
      （``{"type":"progress"|"token"|"notice", ...}``）
    - ``event: node``   —— 某个节点执行完毕
    - ``event: done``   —— 完成，``data`` 与 ``POST /api/chat`` 的响应同构
    - ``event: error``  —— 出错，``code`` 与统一错误契约一致

    注意：会话/分支校验仍在**开始流之前**完成，因此 404 之类会正常返回 HTTP 状态码，
    而不是塞一个错误进流里 —— 那样前端很难区分「请求错了」和「跑到一半失败」。
    """
    if await store.get_session(payload.session_id) is None:
        raise SessionNotFound()

    branch_id = await _resolve_branch(store, payload.session_id, payload.branch_id)
    service = ChatService(graph=graph, store=store)
    message = payload.message

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for item in service.send_stream(
                session_id=payload.session_id,
                branch_id=branch_id,
                message=message,
                detail_level=payload.detail_level,
            ):
                if item["event"] == "done":
                    outcome = item["data"]
                    await _autoname_if_untitled(store, payload.session_id, message)
                    yield _sse(
                        "done",
                        {
                            "session_id": payload.session_id,
                            "branch_id": outcome.branch_id,
                            "version_id": outcome.version_id,
                            "status": outcome.status,
                            "reply": outcome.reply,
                            "plan": outcome.plan_markdown,
                            "missing_fields": outcome.missing_fields or [],
                        },
                    )
                else:
                    yield _sse(item["event"], item["data"])
        except AppError as exc:
            logger.warning("流式对话中断: %s", exc.message)
            yield _sse("error", {"code": exc.code, "message": exc.message})
        except Exception as exc:  # pragma: no cover - 兜底，避免流悬挂
            logger.exception("流式对话异常")
            yield _sse("error", {"code": "INTERNAL_ERROR", "message": f"服务内部错误：{exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 关掉 Nginx 等反代的缓冲，否则流式会被攒成一坨再发
            "X-Accel-Buffering": "no",
        },
    )
