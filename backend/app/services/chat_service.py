"""对话服务 —— 把「编排图」与「持久化」粘在一起。

职责边界：
- 图负责**怎么想**（收集 → 检索 → 生成 → 修订）；
- 本服务负责**记下来**（每个产出落成一个版本，供版本树 / 分支对比 / 导出使用）。

刻意保持薄：不写业务规则，只做调用编排与持久化。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.checkpoint import thread_config
from app.core.errors import InvalidRequest
from app.core.logging import get_logger
from app.domain.clarification import clarification_prompt
from app.domain.enums import DetailLevel, PlanStatus
from app.domain.models import Requirements
from app.services.branch_lock import branch_guard
from app.services.plan_undo import recover_plan_undo
from app.services.planning_context import planning_context
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
    clarification: dict | None = None
    planning_context: dict | None = None


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
        request_id: str | None = None,
    ) -> ChatOutcome:
        async with branch_guard(branch_id):
            await recover_plan_undo(self._store, self._graph, branch_id)
            return await self._send(
                session_id=session_id,
                branch_id=branch_id,
                message=message,
                detail_level=detail_level,
                request_id=request_id,
            )

    async def send_stream(
        self,
        *,
        session_id: str,
        branch_id: str,
        message: str,
        detail_level: DetailLevel | None = None,
        request_id: str | None = None,
    ) -> AsyncIterator[dict]:
        async with branch_guard(branch_id):
            await recover_plan_undo(self._store, self._graph, branch_id)
            async for item in self._send_stream(
                session_id=session_id,
                branch_id=branch_id,
                message=message,
                detail_level=detail_level,
                request_id=request_id,
            ):
                yield item

    async def _send(
        self,
        *,
        session_id: str,
        branch_id: str,
        message: str,
        detail_level: DetailLevel | None = None,
        request_id: str | None = None,
    ) -> ChatOutcome:
        """把用户消息送入图，并把产出记录为一个新版本。"""
        # 显式声明「非流式」：节点据此选择结构化输出而非逐字流
        config = thread_config(branch_id, stream_llm=False)
        before = await self._graph.aget_state(config)
        previous_status = (before.values or {}).get("status")
        receipt = self._request_receipt(request_id, message, detail_level)
        replay = await self._replay(session_id, branch_id, config, before.values or {}, receipt)
        if replay:
            return replay

        # 只传「本次输入」：历史状态由 checkpointer 自动恢复，
        # messages 通过 add_messages reducer 追加。
        payload: dict = {
            "messages": [
                HumanMessage(content=message, id="request-" + request_id if request_id else None)
            ],
            "chat_request": receipt,
        }
        if detail_level is not None:
            payload["detail_level"] = detail_level

        await self._apply_initial_preferences(session_id, before.values or {}, payload)

        values = await self._graph.ainvoke(payload, config)
        values = dict(values)

        return await self._finish(session_id, branch_id, values, config, receipt, previous_status)

    async def _send_stream(
        self,
        *,
        session_id: str,
        branch_id: str,
        message: str,
        detail_level: DetailLevel | None = None,
        request_id: str | None = None,
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
        before = await self._graph.aget_state(config)
        previous_status = (before.values or {}).get("status")
        receipt = self._request_receipt(request_id, message, detail_level)
        replay = await self._replay(session_id, branch_id, config, before.values or {}, receipt)
        if replay:
            yield {"event": "done", "data": replay}
            return

        payload: dict = {
            "messages": [
                HumanMessage(content=message, id="request-" + request_id if request_id else None)
            ],
            "chat_request": receipt,
        }
        if detail_level is not None:
            payload["detail_level"] = detail_level

        await self._apply_initial_preferences(session_id, before.values or {}, payload)

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

        outcome = await self._finish(
            session_id, branch_id, values, config, receipt, previous_status
        )
        yield {"event": "done", "data": outcome}

    async def _apply_initial_preferences(
        self, session_id: str, values: dict, payload: dict
    ) -> None:
        # 只在空白会话首次输入前使用创建时明确选用的副本，不读取会变化的常用偏好。
        if values.get("messages") or values.get("plan_markdown"):
            return
        current = values.get("requirements") or Requirements()
        if current != Requirements():
            return
        session = await self._store.get_session(session_id)
        if session and session.initial_preferences:
            payload["requirements"] = Requirements(preferences=session.initial_preferences)

    @staticmethod
    def _request_receipt(request_id: str | None, message: str, level: DetailLevel | None):
        if not request_id:
            return None
        fingerprint = hashlib.sha256(
            json.dumps([message, level], ensure_ascii=False).encode()
        ).hexdigest()
        return {"id": request_id, "fingerprint": fingerprint}

    async def _replay(self, session_id, branch_id, config, values, receipt):
        completed = values.get("completed_chat")
        if completed and not await self._store.get_chat_receipt(branch_id, completed["id"]):
            # 先收妥上次已完成的结果，再允许新消息覆盖完成标记。
            await self._finish(
                session_id, branch_id, values, config, completed, values.get("status")
            )
        if not receipt:
            return None
        cached = await self._store.get_chat_receipt(branch_id, receipt["id"])
        previous = (
            cached[0]
            if cached
            else (
                completed["fingerprint"] if completed and completed["id"] == receipt["id"] else None
            )
        )
        if previous is not None and previous != receipt["fingerprint"]:
            raise InvalidRequest("这次发送的内容已改变，请作为一条新消息发送。")
        if cached:
            await self._store.touch_session(session_id)
            return ChatOutcome(**cached[1])
        if previous is not None:
            # 图已提交但回执尚未保存（如断流／退出），从该完成状态补齐结果，不能重跑记账。
            return await self._finish(
                session_id, branch_id, values, config, receipt, values.get("status")
            )
        return None

    async def _finish(self, session_id, branch_id, values, config, receipt, previous_status):
        version_id = await self.record_version(
            branch_id, values, config, previous_status=previous_status
        )
        await self._store.touch_session(session_id)
        outcome = ChatOutcome(
            branch_id=branch_id,
            planning_context=planning_context(values).model_dump(mode="json"),
            status=str(values.get("status") or PlanStatus.COLLECTING),
            reply=_last_ai_text(values) or "（无回复内容）",
            plan_markdown=values.get("plan_markdown") or "",
            version_id=version_id,
            missing_fields=list(values.get("missing_fields") or []),
            clarification=clarification_prompt(
                values.get("pending_journey")
                or values.get("pending_place")
                or values.get("pending_linked")
                or values.get("pending_move")
                or values.get("pending_replan")
            ),
        )
        if receipt:
            await self._store.save_chat_receipt(
                branch_id, receipt["id"], receipt["fingerprint"], asdict(outcome)
            )
        return outcome

    async def record_version(
        self,
        branch_id: str,
        values: dict,
        config: dict,
        *,
        trigger: str = "user_message",
        summary: str | None = None,
        previous_status: str | None = None,
    ) -> str | None:
        """有路书产出时，落一个版本记录（供版本树与导出使用）。"""
        markdown = values.get("plan_markdown") or ""
        if not markdown:
            return None

        # 澄清、取消和无变更回复只存对话 checkpoint，不制造相同的路书版本。
        research = values.get("research")
        citations = research.build_citations() if research else []
        if trigger == "user_message" and values.get("status") == previous_status:
            versions = await self._store.list_versions(branch_id)
            latest = await self._store.get_version_detail(versions[-1].id) if versions else None
            if (
                latest
                and latest.markdown == markdown
                and latest.itinerary == values.get("itinerary")
                and latest.requirements == values.get("requirements")
                and latest.todos == list(values.get("todos") or [])
                and latest.citations == citations
                and latest.version.detail_level
                == str(values.get("detail_level") or DetailLevel.L2_STANDARD.value)
            ):
                return latest.version.id

        checkpoint_id = None
        try:
            snapshot = await self._graph.aget_state(config)
            checkpoint_id = (snapshot.config.get("configurable") or {}).get("checkpoint_id")
        except Exception:  # pragma: no cover - 仅影响历史回溯，不影响主流程
            logger.warning("未能读取 checkpoint_id，版本仍会记录")

        version = await self._store.create_version(
            branch_id,
            summary=summary or _summarise(values),
            detail_level=str(values.get("detail_level") or DetailLevel.L2_STANDARD.value),
            trigger=trigger,
            checkpoint_id=checkpoint_id,
        )
        await self._store.save_plan(
            version.id,
            markdown=markdown,
            citations=citations,
            todos=list(values.get("todos") or []),
            searched_at=getattr(research, "searched_at", None),
            itinerary=values.get("itinerary"),
            requirements=values.get("requirements"),
        )
        logger.info(
            "版本已记录",
            extra={
                "extra_fields": {"branch": branch_id, "seq": version.seq, "chars": len(markdown)}
            },
        )
        return version.id


__all__ = ["ChatOutcome", "ChatService"]
