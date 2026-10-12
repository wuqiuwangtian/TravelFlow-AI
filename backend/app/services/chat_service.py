"""对话服务 —— 把「编排图」与「持久化」粘在一起。

职责边界：
- 图负责**怎么想**（收集 → 检索 → 生成 → 修订）；
- 本服务负责**记下来**（每个产出落成一个版本，供版本树 / 分支对比 / 导出使用）。

刻意保持薄：不写业务规则，只做调用编排与持久化。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.checkpoint import thread_config
from app.core.logging import get_logger
from app.domain.enums import DetailLevel, PlanStatus
from app.storage.session_store import SessionStore

logger = get_logger(__name__)


@dataclass(slots=True)
class ChatOutcome:
    """一轮对话的结果。"""

    branch_id: str
    status: str
    reply: str
    plan_markdown: str = ""
    version_id: str | None = None
    missing_fields: list[str] | None = None


def _last_ai_text(values: dict) -> str:
    for message in reversed(values.get("messages", [])):
        if isinstance(message, AIMessage) or getattr(message, "type", "") == "ai":
            content = getattr(message, "content", "")
            if isinstance(content, str) and content:
                return content
    return ""


def _summarise(values: dict) -> str:
    """给版本生成一句人话摘要（版本树不用点开就能看懂）。"""
    status = values.get("status")
    if status == PlanStatus.COLLECTING:
        return "需求收集中"
    if status == PlanStatus.FINISHED:
        return "计划已确认"
    markdown = values.get("plan_markdown") or ""
    if markdown and values.get("revision", 0):
        return f"第 {values['revision']} 次修改"
    if markdown:
        return "路书已生成"
    return "检索完成"


class ChatService:
    """对话编排与持久化。"""

    def __init__(self, *, graph, store: SessionStore) -> None:
        self._graph = graph
        self._store = store

    async def send(
        self,
        *,
        session_id: str,
        branch_id: str,
        message: str,
        detail_level: DetailLevel | None = None,
    ) -> ChatOutcome:
        """把用户消息送入图，并把产出记录为一个新版本。"""
        # 显式声明「非流式」：节点据此选择结构化输出而非逐字流
        config = thread_config(branch_id, stream_llm=False)

        # 只传「本次输入」：历史状态由 checkpointer 自动恢复，
        # messages 通过 add_messages reducer 追加。
        payload: dict = {"messages": [HumanMessage(content=message)]}
        if detail_level is not None:
            payload["detail_level"] = detail_level

        values = await self._graph.ainvoke(payload, config)
        values = dict(values)

        version_id = await self._record_version(branch_id, values, config)
        await self._store.touch_session(session_id)

        return ChatOutcome(
            branch_id=branch_id,
            status=str(values.get("status") or PlanStatus.COLLECTING),
            reply=_last_ai_text(values) or "（无回复内容）",
            plan_markdown=values.get("plan_markdown") or "",
            version_id=version_id,
            missing_fields=list(values.get("missing_fields") or []),
        )

    async def send_stream(
        self,
        *,
        session_id: str,
        branch_id: str,
        message: str,
        detail_level: DetailLevel | None = None,
    ) -> AsyncIterator[dict]:
        """流式版本：边执行边产出事件，最后给出与 ``send`` 相同的结果。

        产出的事件（``event`` 字段）：
        - ``custom`` —— 节点内部推送的进度 / 增量文本
        - ``node``   —— 某个节点刚刚执行完
        - ``done``   —— 全部完成，``data`` 是 ``ChatOutcome`` 的字典形式

        为什么最后要重新取一次 state：``stream_mode="updates"`` 只给每个节点的
        增量，拿不到完整状态；而版本落库需要完整的 ``plan_markdown`` 与检索结果。
        """
        config = thread_config(branch_id, stream_llm=True)

        payload: dict = {"messages": [HumanMessage(content=message)]}
        if detail_level is not None:
            payload["detail_level"] = detail_level

        async for mode, chunk in self._graph.astream(
            payload, config, stream_mode=["custom", "updates"]
        ):
            if mode == "custom":
                yield {"event": "custom", "data": chunk}
            elif mode == "updates" and isinstance(chunk, dict):
                for node_name in chunk:
                    yield {"event": "node", "data": {"node": node_name}}

        snapshot = await self._graph.aget_state(config)
        values = dict(snapshot.values or {})

        version_id = await self._record_version(branch_id, values, config)
        await self._store.touch_session(session_id)

        outcome = ChatOutcome(
            branch_id=branch_id,
            status=str(values.get("status") or PlanStatus.COLLECTING),
            reply=_last_ai_text(values) or "（无回复内容）",
            plan_markdown=values.get("plan_markdown") or "",
            version_id=version_id,
            missing_fields=list(values.get("missing_fields") or []),
        )
        yield {"event": "done", "data": outcome}

    async def _record_version(self, branch_id: str, values: dict, config: dict) -> str | None:
        """有路书产出时，落一个版本记录（供版本树与导出使用）。"""
        markdown = values.get("plan_markdown") or ""
        if not markdown:
            return None

        checkpoint_id = None
        try:
            snapshot = await self._graph.aget_state(config)
            checkpoint_id = (snapshot.config.get("configurable") or {}).get("checkpoint_id")
        except Exception:  # pragma: no cover - 仅影响历史回溯，不影响主流程
            logger.warning("未能读取 checkpoint_id，版本仍会记录")

        research = values.get("research")
        version = await self._store.create_version(
            branch_id,
            summary=_summarise(values),
            detail_level=str(values.get("detail_level") or DetailLevel.L2_STANDARD.value),
            trigger="user_message",
            checkpoint_id=checkpoint_id,
        )
        await self._store.save_plan(
            version.id,
            markdown=markdown,
            citations=research.build_citations() if research else [],
            todos=list(values.get("todos") or []),
            searched_at=getattr(research, "searched_at", None),
        )
        logger.info(
            "版本已记录",
            extra={
                "extra_fields": {"branch": branch_id, "seq": version.seq, "chars": len(markdown)}
            },
        )
        return version.id


__all__ = ["ChatOutcome", "ChatService"]
