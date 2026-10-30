"""撤销最近一次对话规划：追加版本，只恢复规划，保留真实旅行记录。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from app.agents.checkpoint import thread_config
from app.core.errors import ConflictError, InvalidRequest
from app.domain.enums import PlanStatus
from app.domain.models import Requirements, ResearchBundle, VersionDetail
from app.search.base import SearchResult
from app.services.itinerary_service import sync_markdown
from app.storage.session_store import SessionStore


class PlanChange(BaseModel):
    version_id: str
    previous_version_id: str
    current_version_id: str
    summary: str
    changes: list[str]
    blocked_reason: str | None = None


class UndoPlanInput(BaseModel):
    request_id: str = Field(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    change_version_id: str = Field(min_length=1, max_length=100)


def _planning(detail: VersionDetail) -> dict:
    """费用、回款、回顾与游玩状态不构成新的规划；不扫描更早的任意历史回退。"""
    assert detail.itinerary is not None
    itinerary = detail.itinerary.model_dump(mode="json", exclude_computed_fields=True)
    for field in ("expenses", "adjustments", "recap", "ended_at"):
        itinerary.pop(field, None)
    for day in itinerary["days"]:
        day["warnings"] = list(dict.fromkeys(day["warnings"]))
        day["schedule_notes"] = list(dict.fromkeys(day["schedule_notes"]))
        for activity in day["activities"]:
            activity.pop("progress", None)
            activity.pop("progress_updated_at", None)
    # 这三节由结构化数据渲染；消费保存会刷新正文，但不能据此提供“撤销消费”。
    narrative = re.sub(
        r"^## (?:四[、.． ]*)?每日详细行程[^\n]*\n.*?(?=^## |\Z)"
        r"|^## 五[、.． ]*费用预估[^\n]*\n.*?(?=^## |\Z)"
        r"|^## 旅中记录与实际消费[^\n]*\n.*?(?=^## |\Z)",
        "",
        detail.markdown,
        flags=re.M | re.S,
    ).strip()
    return {
        "itinerary": itinerary,
        "requirements": detail.requirements,
        "todos": detail.todos,
        "citations": detail.citations,
        "detail_level": detail.version.detail_level,
        "narrative": narrative,
    }


def _restore(before: VersionDetail, current: VersionDetail) -> VersionDetail:
    restored = before.model_copy(deep=True)
    assert restored.itinerary is not None and current.itinerary is not None
    itinerary = restored.itinerary
    # 不重写金额、付款日期、凭证、退款／报销、照片及回顾。
    records = current.itinerary.model_copy(deep=True)
    for field in ("expenses", "adjustments", "recap", "ended_at"):
        setattr(itinerary, field, getattr(records, field))
    old_activities = {a.id: (d.day, a) for d in itinerary.days for a in d.activities}
    for day in current.itinerary.days:
        for activity in day.activities:
            found = old_activities.get(activity.id)
            original = found[1] if found else None
            original_day = found[0] if found else None
            protected = activity.progress != "not_started" or activity.booking is not None
            if protected and (
                original is None
                or original_day != day.day
                or original.model_dump(exclude={"progress", "progress_updated_at"})
                != activity.model_dump(exclude={"progress", "progress_updated_at"})
            ):
                raise ConflictError(
                    "这次调整涉及的站点已有游玩进度或预约，无法直接撤销。"
                    "请在对话中调整后续安排，已有记录会保留。"
                )
            if original:
                original.progress = activity.progress
                original.progress_updated_at = activity.progress_updated_at
                original.booking = (
                    activity.booking.model_copy(deep=True) if activity.booking else None
                )
    restored.markdown = sync_markdown(
        before.markdown, itinerary, restored.requirements or Requirements()
    )
    return restored


def _changes(before: VersionDetail, after: VersionDetail) -> list[str]:
    assert before.itinerary is not None and after.itinerary is not None
    changes = []
    if before.requirements != after.requirements:
        changes.append("出行需求已更新")
    old = {d.day: d for d in before.itinerary.days}
    new = {d.day: d for d in after.itinerary.days}
    changed_days = [day for day in sorted(old.keys() | new.keys()) if old.get(day) != new.get(day)]
    if changed_days:
        changes.append("第 " + "、".join(map(str, changed_days)) + " 天安排已更新")
        for number in changed_days[:2]:
            prior = old.get(number)
            revised = new.get(number)
            if prior is None or revised is None:
                changes.append(f"{'新增' if revised else '移除'}第 {number} 天")
                continue
            old_titles = "、".join(a.title for a in prior.activities)
            new_titles = "、".join(a.title for a in revised.activities)
            if old_titles != new_titles:
                changes.append(f"第 {number} 天：原「{old_titles[:90]}」 → 现「{new_titles[:90]}」")
    if not changes:
        changes.append("计划书内容已更新")
    changes.append("路线、预估与计划书已同步，已记消费保留")
    return changes


async def current_change(store: SessionStore, version_id: str) -> PlanChange | None:
    current = await store.get_version_detail(version_id)
    if current is None:
        raise InvalidRequest("版本不存在")
    versions = await store.list_versions(current.version.branch_id)
    if (
        not versions
        or versions[-1].id != version_id
        or not current.itinerary
        or not current.itinerary.days
    ):
        return None
    after = current
    for version in reversed(versions[:-1]):
        # 撤销本身不变成可反复切换的旧规划；分叉、工程采用仍走各自版本流程。
        if after.version.trigger.startswith("plan_undo:"):
            return None
        before = await store.get_version_detail(version.id)
        if before is None or not before.itinerary or not before.itinerary.days:
            return None
        if _planning(before) != _planning(after):
            if after.version.trigger != "user_message":
                return None
            blocker = None
            try:
                _restore(before, current)
            except ConflictError as exc:
                blocker = str(exc)
            return PlanChange(
                version_id=after.version.id,
                previous_version_id=before.version.id,
                current_version_id=current.version.id,
                summary=f"已更新规划 · v{after.version.seq}",
                changes=_changes(before, after),
                blocked_reason=blocker,
            )
        after = before
    return None


async def recover_plan_undo(store: SessionStore, graph, branch_id: str) -> VersionDetail | None:
    """checkpoint 已落盘但版本写入中断时，先补齐同一版本，再接受下一次写入。需持分支锁。"""
    snapshot = await graph.aget_state(thread_config(branch_id))
    marker = (snapshot.values or {}).get("completed_plan_undo")
    if not marker:
        return None
    result = await store.get_version_detail(marker["result_id"])
    if result and result.markdown:
        return None
    versions = await store.list_versions(branch_id)
    if not versions or versions[-1].id not in (marker["base_id"], marker["result_id"]):
        raise ConflictError("撤销结果尚未整理完成，请重试原撤销操作。")
    source = VersionDetail.model_validate(marker["result"])
    if result is None:
        version = await store.create_version(
            branch_id,
            version_id=marker["result_id"],
            summary=marker["summary"],
            detail_level=source.version.detail_level,
            trigger=marker["trigger"],
            checkpoint_id=snapshot.config["configurable"].get("checkpoint_id"),
        )
    else:
        version = result.version
    await store.save_plan(
        version.id,
        markdown=source.markdown,
        itinerary=source.itinerary,
        requirements=source.requirements,
        citations=source.citations,
        todos=source.todos,
        searched_at=source.searched_at,
    )
    branch = await store.get_branch(branch_id)
    if branch:
        await store.touch_session(branch.session_id)
    return await store.get_version_detail(version.id)


async def undo_plan(store: SessionStore, graph, version_id: str, payload: UndoPlanInput):
    current = await store.get_version_detail(version_id)
    if current is None:
        raise InvalidRequest("版本不存在")
    branch_id = current.version.branch_id
    await recover_plan_undo(store, graph, branch_id)
    fingerprint = hashlib.sha256(
        json.dumps([version_id, payload.change_version_id]).encode()
    ).hexdigest()
    result_id = hashlib.sha256(f"plan_undo:{branch_id}:{payload.request_id}".encode()).hexdigest()[
        :24
    ]
    trigger = "plan_undo:" + fingerprint
    existing = await store.get_version_detail(result_id)
    if existing:
        if existing.version.trigger != trigger or existing.version.branch_id != branch_id:
            raise ConflictError("同一次撤销请求的内容已改变，请刷新后再操作。")
        return existing
    # 如上一条对话完成后仅回执写入中断，必须在改变 checkpoint 内容前收妥它。
    from app.services.chat_service import ChatService

    current_snapshot = await graph.aget_state(thread_config(branch_id))
    branch = await store.get_branch(branch_id)
    assert branch is not None
    await ChatService(graph=graph, store=store)._replay(
        branch.session_id,
        branch_id,
        thread_config(branch_id),
        dict(current_snapshot.values or {}),
        None,
    )
    offer = await current_change(store, version_id)
    if offer is None or offer.version_id != payload.change_version_id:
        raise ConflictError("规划已变化，这次修改已不能直接撤销。请刷新查看当前安排。")
    if offer.blocked_reason:
        raise ConflictError(offer.blocked_reason)
    snapshot = await graph.aget_state(thread_config(branch_id))
    values = dict(snapshot.values or {})
    if snapshot.next or any(
        values.get(key)
        for key in (
            "pending_journey",
            "pending_place",
            "pending_linked",
            "pending_move",
            "pending_replan",
        )
    ):
        raise ConflictError("请先完成或取消对话中的待确认操作，再撤销规划。")
    if (
        values.get("itinerary") != current.itinerary
        or values.get("plan_markdown") != current.markdown
        or values.get("requirements") != current.requirements
        or list(values.get("todos") or []) != current.todos
    ):
        raise ConflictError("对话或方案已变化，请刷新后再撤销。")
    before = await store.get_version_detail(offer.previous_version_id)
    assert before is not None
    restored = _restore(before, current)
    research = None
    if before.version.checkpoint_id:
        old_snapshot = await graph.aget_state(
            thread_config(branch_id, checkpoint_id=before.version.checkpoint_id)
        )
        research = (old_snapshot.values or {}).get("research")
    if research is None and before.citations:
        research = ResearchBundle(
            transport=[
                SearchResult(title=c.title, url=c.url, content="", provider=c.provider)
                for c in before.citations
            ],
            searched_at=before.searched_at or datetime.now(),
        )
    change = await store.get_version(offer.version_id)
    assert change is not None
    summary = f"撤销 v{change.seq} 的规划修改"
    marker = {
        "base_id": version_id,
        "result_id": result_id,
        "trigger": trigger,
        "summary": summary,
        "result": restored.model_dump(mode="json", exclude_computed_fields=True),
    }
    requirements = restored.requirements or Requirements()
    await graph.aupdate_state(
        thread_config(branch_id),
        {
            "plan_markdown": restored.markdown,
            "itinerary": restored.itinerary,
            "requirements": requirements,
            "research": research,
            "todos": restored.todos,
            "detail_level": restored.version.detail_level,
            "missing_fields": requirements.missing_fields(),
            "revision": int(values.get("revision", 0)) + 1,
            "status": PlanStatus.REVIEWING,
            "completed_plan_undo": marker,
            "messages": [
                AIMessage(
                    id="plan-undo-" + result_id,
                    content="已撤销这次规划修改，路线、预估和计划书已同步；实际消费与旅行记录保留。",
                )
            ],
            "updated_at": datetime.now(),
        },
        as_node="refiner",
    )
    result = await recover_plan_undo(store, graph, branch_id)
    assert result is not None
    return result
