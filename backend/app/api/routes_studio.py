"""自由生成工程的保存、校验与执行；不会自动覆盖正式行程。"""

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.deps import get_store
from app.core.errors import BranchNotFound, ConflictError
from app.domain.workbench import SaveStudio, StudioGraph, StudioProject, StudioRun, StudioRunInput
from app.llm.factory import create_llm_provider
from app.search.factory import create_search_provider
from app.services.branch_lock import branch_busy, branch_guard
from app.services.studio_engine import execution_order, run_graph, topology
from app.services.studio_templates import templates
from app.storage.session_store import SessionStore

router = APIRouter(prefix="/api", tags=["studio"])
_running: dict[tuple[str, str], asyncio.Task] = {}


async def branch_exists(store: SessionStore, branch_id: str) -> None:
    if not await store.get_branch(branch_id):
        raise BranchNotFound()


@router.get("/workflow/templates")
async def get_templates():
    return templates()


@router.post("/workflow/validate", response_model=StudioGraph)
async def validate_project(graph: StudioGraph):
    topology(graph)
    return graph


@router.get("/branches/{branch_id}/studio", response_model=StudioProject)
async def get_project(branch_id: str, store: SessionStore = Depends(get_store)):
    await branch_exists(store, branch_id)
    project = await store.workbench.project(branch_id)
    project.runs = await store.workbench.runs(branch_id)
    if not branch_busy("studio:" + branch_id):
        for run in project.runs:
            if run.status == "running":
                run.status, run.finished_at = "interrupted", datetime.now(UTC)
                await store.workbench.write_run(run)
    versions = await store.list_versions(branch_id)
    if versions:
        detail = await store.get_version_detail(versions[-1].id)
        if detail and detail.requirements:
            requirements = detail.requirements
            values = [
                f"目的地：{requirements.destination or '尚未确定'}",
                f"出发地：{requirements.origin or '待补充'}",
                f"旅行天数：{requirements.duration_days or '待确定'}",
                f"人数：{requirements.travelers or '待确定'}",
                f"预算：{requirements.budget if requirements.budget is not None else '待确定'}元"
                + {"total": "（全团）", "per_person": "（人均）"}.get(
                    requirements.budget_basis or "", "（口径待确定）"
                ),
                f"出发日期：{requirements.departure_date or '尚未确定'}",
                f"偏好：{'、'.join(requirements.preferences) or '暂无特别要求'}",
            ]
            project.requirements_seed = "\n".join(values)
    return project


@router.put("/branches/{branch_id}/studio", response_model=StudioProject)
async def save_project(
    branch_id: str, payload: SaveStudio, store: SessionStore = Depends(get_store)
):
    await branch_exists(store, branch_id)
    topology(payload.graph)
    async with branch_guard("studio:" + branch_id):
        return await store.workbench.save(branch_id, payload.graph, payload.expected_revision)


@router.post("/branches/{branch_id}/studio/runs", response_model=StudioRun)
async def execute_project(
    branch_id: str,
    payload: StudioRunInput,
    store: SessionStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
):
    await branch_exists(store, branch_id)
    existing = await store.workbench.run(branch_id, payload.request_id)
    if existing:
        if (existing.revision, existing.target_node, existing.force) != (
            payload.expected_revision,
            payload.target_node,
            payload.force,
        ):
            raise ConflictError("同一次运行请求的参数已变化，请重新运行。")
        return existing
    async with branch_guard("studio:" + branch_id):
        project = await store.workbench.project(branch_id)
        if project.revision != payload.expected_revision or project.graph is None:
            raise ConflictError("工程版本已变化，请载入最新工程后运行。")
        order = execution_order(project.graph, payload.target_node)
        kinds = {n.kind for n in project.graph.nodes if n.id in order}
        llm = create_llm_provider(settings) if "generate" in kinds else None
        search = create_search_provider(settings) if "search" in kinds else None
        # 外层限制整个工程；节点进度与已成功产物持久化，断流后可读取并重跑。
        task = asyncio.create_task(
            run_graph(
                branch_id,
                project.revision,
                project.graph,
                payload,
                store.workbench,
                settings,
                llm,
                search,
            )
        )
        key = (branch_id, payload.request_id)
        _running[key] = task
        try:
            async with asyncio.timeout(210):
                return await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current and current.cancelling():
                raise
            return await store.workbench.run(branch_id, payload.request_id)
        except TimeoutError:
            return await store.workbench.run(branch_id, payload.request_id)
        finally:
            _running.pop(key, None)


@router.post("/branches/{branch_id}/studio/runs/{run_id}/stop")
async def stop_run(branch_id: str, run_id: str, store: SessionStore = Depends(get_store)):
    await branch_exists(store, branch_id)
    task = _running.get((branch_id, run_id))
    if task and not task.done():
        task.cancel()
    return {"message": "停止请求已处理，已完成的节点产物会保留。"}
