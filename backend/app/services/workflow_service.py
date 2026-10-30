"""从当前检查点或指定业务版本读取节点数据，不调用模型、检索或执行图。"""

from datetime import UTC, datetime
from typing import Literal

from app.agents.checkpoint import thread_config
from app.core.errors import BranchNotFound, InvalidRequest, SessionNotFound
from app.domain.enums import PlanStatus
from app.domain.itinerary import Itinerary
from app.domain.models import Requirements, ResearchBundle
from app.domain.workflow import NodeId, WorkflowEdge, WorkflowFact, WorkflowNode, WorkflowView
from app.services.branch_lock import branch_busy
from app.services.budget_service import budget_overview
from app.storage.session_store import SessionStore

NODE_IDS: tuple[NodeId, ...] = ("collector", "researcher", "planner", "refiner")
REQUIRED_LABELS = {
    "destination": "目的地",
    "origin": "出发地",
    "duration_days": "天数",
    "budget": "预算",
}


def fact(label: str, value: object) -> WorkflowFact:
    return WorkflowFact(label=label, value=str(value))


def money(value: int) -> str:
    return f"¥{value / 100:.2f}"


def _nodes(values: dict, citations: int, version_count: int) -> list[WorkflowNode]:
    req = values.get("requirements") or Requirements()
    itinerary: Itinerary | None = values.get("itinerary")
    research: ResearchBundle | None = values.get("research")
    has_plan = bool(values.get("plan_markdown") or itinerary)
    missing = req.missing_fields()
    collected = [
        fact("目的地", req.destination or "待确定"),
        fact("出发地", req.origin or "待补充"),
        fact("天数", f"{req.duration_days}天" if req.duration_days else "待补充"),
        fact("预算", f"¥{req.budget:.2f}" if req.budget is not None else "待补充"),
        fact(
            "预算口径",
            {"per_person": "人均", "total": "全团"}.get(req.budget_basis or "", "未明确"),
        ),
        fact("人数", req.travelers or "未明确"),
        fact("出行日期", req.departure_date or "未定"),
        fact("本次偏好", "、".join(req.preferences) or "无已记录偏好"),
        fact("仍需补充", "、".join(REQUIRED_LABELS[name] for name in missing) or "必填需求已齐全"),
    ]
    ideas = values.get("destination_ideas") or []
    if ideas:
        collected.append(fact("候选城市", "、".join(ideas)))
    has_research = bool(research or citations)
    research_facts = [fact("已保留引用", f"{citations}条")]
    if research:
        research_facts.extend(
            [
                fact("检索词", f"{len(research.queries)}组"),
                fact("交通资料", f"{len(research.transport)}条"),
                fact("景点资料", f"{len(research.attractions)}条"),
                fact("评价与避雷资料", f"{len(research.reviews)}条"),
                fact("资料取得时间", research.searched_at.isoformat()),
            ]
        )
    research_notes = ["资料数量不代表已核实营业、价格或可预订情况。"]
    if not research:
        research_notes.append("未保留完整检索过程，已有路书也不据此推断检索节点成功运行。")
    plan_facts = [fact("文字路书", "已有内容" if values.get("plan_markdown") else "尚无内容")]
    if itinerary:
        activities = [
            a for day in itinerary.days for a in day.activities if not a.is_context_anchor
        ]
        legs = [leg for day in itinerary.days for leg in day.legs]
        budget = budget_overview(itinerary, req)
        located_count = sum(a.place is not None and a.place_status == "matched" for a in activities)
        plan_facts.extend(
            [
                fact("结构化行程", f"{len(itinerary.days)}天 · {len(activities)}项活动"),
                fact(
                    "确认坐标",
                    f"{located_count}项",
                ),
                fact(
                    "交通段",
                    f"{len(legs)}段，其中{sum(leg.status == 'ok' for leg in legs)}段有路线结果",
                ),
                fact("预估已知部分", money(budget.planned_cents)),
                fact("预估未知项", f"{budget.unknown_count}项"),
                fact("预计总支出", money(budget.projected_cents) + "（仍可能有未列开支）"),
                fact("已记净消费", money(itinerary.ledger_summary.net_expense_cents)),
            ]
        )
    elif has_plan:
        plan_facts.append(fact("结构化行程", "尚未整理，文字与结构化数据分开展示"))
    pending = any(
        values.get(key)
        for key in (
            "pending_journey",
            "pending_place",
            "pending_linked",
            "pending_move",
            "pending_replan",
        )
    )
    edit_facts = [
        fact("本方案保存版本", f"{version_count}份"),
        fact(
            "计划确认",
            "已确认"
            if values.get("status") == PlanStatus.FINISHED
            else "当前待确认"
            if values.get("status")
            else "未保留确认状态",
        ),
        fact(
            "待确认操作",
            "存在待澄清或待应用的操作，请回原对话继续" if pending else "暂无已记录的待确认操作",
        ),
    ]
    if itinerary:
        completed_count = sum(
            a.progress == "completed"
            for d in itinerary.days
            for a in d.activities
            if not a.is_context_anchor
        )
        edit_facts.extend(
            [
                fact(
                    "活动执行",
                    f"已完成{completed_count}项",
                ),
                fact("实付记录", f"{len(itinerary.expenses)}笔"),
                fact("旅行状态", "已结束，仍可补记" if itinerary.ended_at else "尚未记录结束"),
            ]
        )
    return [
        WorkflowNode(
            id="collector",
            title="需求与城市推荐",
            description="收集必要需求；用户需要时给出城市候选，再由用户确定目的地。",
            state="needs_input" if missing else "available",
            inputs=["本次对话", "创建时主动选用的偏好"],
            outputs=collected,
            notes=["候选城市和计划目的地都不代表实际到访。"],
        ),
        WorkflowNode(
            id="researcher",
            title="资料检索",
            description="根据需求查找交通、景点和避雷资料，供生成或修改时参考。",
            state="available"
            if has_research
            else "unknown"
            if has_plan
            else "ready"
            if not missing
            else "waiting",
            inputs=["目的地、出发地、天数与预算", "明确的出行日期或修改要求"],
            outputs=research_facts,
            notes=research_notes,
        ),
        WorkflowNode(
            id="planner",
            title="行程、路线与预算",
            description="同一次生成同时产出每日行程、路线、预算和完整路书。",
            state="available" if has_plan else "ready" if has_research else "waiting",
            inputs=["本次出行需求", "已取得的资料与引用"],
            outputs=plan_facts,
            notes=[
                "行程、路线与预算是这一节点的输出，目前并非可单独执行的三个节点。",
                "未知坐标或费用保持未知，不影响查看已有内容。",
            ],
        ),
        WorkflowNode(
            id="refiner",
            title="确认与修改",
            description="后续对话处理确认、调整及旅中记录，已有人工记录继续保留。",
            state="needs_input" if pending else "ready" if has_plan else "waiting",
            inputs=["已有路书和结构化行程", "本次修改要求、人工预算与实际记录"],
            outputs=edit_facts,
            notes=["等待下一条对话，不自动重复生成；待应用预览只在原流程确认后生效。"],
        ),
    ]


async def workflow_view(
    store: SessionStore, graph, branch_id: str, version_id: str | None
) -> WorkflowView:
    branch = await store.get_branch(branch_id)
    if branch is None:
        raise BranchNotFound()
    session = await store.get_session(branch.session_id)
    if session is None:
        raise SessionNotFound()
    versions = await store.list_versions(branch_id)
    historical = version_id is not None
    version = (
        next((item for item in versions if item.id == version_id), None)
        if historical
        else (versions[-1] if versions else None)
    )
    if historical and version is None:
        raise InvalidRequest("所选版本不存在或不属于此方案。")
    detail = await store.get_version_detail(version.id) if version else None
    values: dict = {}
    checkpoint_status: Literal["available", "missing", "unavailable", "not_configured"] = (
        "not_configured" if graph is None else "missing"
    )
    checkpoint_at = None
    snapshot = None
    warnings = []
    if graph is not None and (not historical or (version and version.checkpoint_id)):
        config = thread_config(branch_id)
        if historical and version:
            config = thread_config(branch_id, checkpoint_id=version.checkpoint_id)
        try:
            snapshot = await graph.aget_state(config)
            values = dict(snapshot.values or {})
            if values:
                checkpoint_status = "available"
                if snapshot.created_at:
                    checkpoint_at = datetime.fromisoformat(snapshot.created_at)
        except Exception:
            values = {}
            snapshot = None
            checkpoint_at = None
            checkpoint_status = "unavailable"
            warnings.append("过程记录暂不可读取，显示已保存数据；未重新执行规划。")
    source: Literal["checkpoint", "saved_version", "empty"] = (
        "checkpoint" if values else "saved_version" if detail else "empty"
    )
    # 指定版本的业务快照是行程和需求的依据；绝不拿当前分支状态补旧版本。
    if detail and (historical or not values):
        values.update(
            requirements=detail.requirements or Requirements(),
            itinerary=detail.itinerary,
            plan_markdown=detail.markdown,
            todos=detail.todos,
        )
    if not values:
        values["requirements"] = Requirements(preferences=session.initial_preferences)
    citations = (
        len(detail.citations)
        if detail and (historical or source == "saved_version")
        else len(values["research"].build_citations())
        if values.get("research")
        else 0
    )
    nodes = _nodes(values, citations, version.seq if historical and version else len(versions))
    focus: NodeId = (
        "planner" if values.get("plan_markdown") or values.get("itinerary") else "collector"
    )
    if not historical and values.get("status") == PlanStatus.RESEARCHING:
        focus = "researcher"
    elif not historical and values.get("status") == PlanStatus.PLANNING:
        focus = "planner"
    interrupted = set()
    if snapshot and checkpoint_status == "available":
        interrupted = {task.name for task in snapshot.tasks if task.error or task.interrupts}
        for node in nodes:
            if node.id in interrupted:
                node.state = "interrupted"
                node.notes.append(
                    "过程记录显示此节点曾中断。已有数据保留，请回原对话查看提示并重试。"
                )
                focus = node.id
    if nodes[-1].state == "needs_input" and not interrupted:
        focus = "refiner"
    if checkpoint_status != "available":
        warnings.append("没有可用的过程记录，节点仅按已有数据展示；未记录的执行状态保持未知。")
    return WorkflowView(
        session=session,
        branch=branch,
        version=version,
        historical=historical,
        source=source,
        checkpoint_status=checkpoint_status,
        checkpoint_at=checkpoint_at,
        observed_at=datetime.now(UTC),
        busy=branch_busy(branch_id) if not historical else False,
        plan_status=str(values["status"]) if values.get("status") else None,
        nodes=nodes,
        focus_node=focus,
        warnings=warnings,
        edges=[
            WorkflowEdge(
                source="collector", target="researcher", trigger="automatic", label="必填需求齐全后"
            ),
            WorkflowEdge(
                source="researcher", target="planner", trigger="automatic", label="资料返回后"
            ),
            WorkflowEdge(
                source="planner",
                target="refiner",
                trigger="next_message",
                label="生成后的下一轮对话",
            ),
            WorkflowEdge(
                source="collector",
                target="collector",
                trigger="next_message",
                label="补充需求后继续",
            ),
            WorkflowEdge(
                source="refiner",
                target="refiner",
                trigger="next_message",
                label="后续对话继续确认或修改",
            ),
        ],
    )
