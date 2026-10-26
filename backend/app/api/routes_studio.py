"""自由生成工程的保存、校验与执行；不会自动覆盖正式行程。"""

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.deps import get_graph, get_store
from app.core.errors import BranchNotFound, ConflictError, InvalidRequest, LLMTimeout
from app.domain.models import VersionDetail
from app.domain.studio_adoption import AdoptionContext, AdoptionInput, AdoptionPreview
from app.domain.studio_library import (
    DeleteUserTemplate,
    LoadUserTemplate,
    SaveUserTemplate,
    UserTemplate,
    UserTemplateSummary,
)
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


@router.get("/workflow/user-templates", response_model=list[UserTemplateSummary])
async def get_user_templates(store: SessionStore = Depends(get_store)):
    return await store.studio_library.list()


@router.get("/workflow/user-templates/{template_id}", response_model=UserTemplate)
async def get_user_template(template_id: str, store: SessionStore = Depends(get_store)):
    return await store.studio_library.get(template_id)


@router.post("/workflow/user-templates", response_model=UserTemplate)
async def create_user_template(payload: SaveUserTemplate, store: SessionStore = Depends(get_store)):
    if payload.expected_revision:
        raise InvalidRequest("新建模板不能指定旧修订号。")
    async with branch_guard("studio-template-library", wait=True):
        return await store.studio_library.save(payload)


@router.put("/workflow/user-templates/{template_id}", response_model=UserTemplate)
async def update_user_template(
    template_id: str, payload: SaveUserTemplate, store: SessionStore = Depends(get_store)
):
    if not payload.expected_revision:
        raise InvalidRequest("更新模板需要当前修订号。")
    async with branch_guard("studio-template-library", wait=True):
        return await store.studio_library.save(payload, template_id)


@router.delete("/workflow/user-templates/{template_id}", status_code=204)
async def delete_user_template(
    template_id: str, payload: DeleteUserTemplate, store: SessionStore = Depends(get_store)
):
    async with branch_guard("studio-template-library", wait=True):
        await store.studio_library.delete(template_id, payload)


@router.post(
    "/branches/{branch_id}/studio/template-drafts/{template_id}", response_model=StudioGraph
)
async def load_user_template(
    branch_id: str,
    template_id: str,
    payload: LoadUserTemplate,
    store: SessionStore = Depends(get_store),
):
    await branch_exists(store, branch_id)
    template = await store.studio_library.get(template_id)
    if template.revision != payload.expected_revision:
        raise ConflictError("模板已变化，请刷新模板库后再载入。")
    graph = template.graph.model_copy(deep=True)
    seed = await requirements_seed(store, branch_id)
    for node in graph.nodes:
        if node.id in template.trip_input_ids:
            node.parameters.text = seed
    return graph


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
    project.requirements_seed = await requirements_seed(store, branch_id)
    return project


async def requirements_seed(store: SessionStore, branch_id: str) -> str:
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
            return "\n".join(values)
    return ""


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
        llm = create_llm_provider(settings) if kinds & {"generate", "plan", "candidates"} else None
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


@router.get(
    "/branches/{branch_id}/studio/runs/{run_id}/nodes/{node_id}/adoption",
    response_model=AdoptionContext,
)
async def adoption_context(
    branch_id: str,
    run_id: str,
    node_id: str,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
):
    from app.services.studio_adoption import adoption_base, source_artifact

    await branch_exists(store, branch_id)
    _, _, source_key = await source_artifact(store, branch_id, run_id, node_id)
    base = await adoption_base(store, graph, branch_id)
    preview = await store.workbench.adoption(branch_id, run_id=run_id, node_id=node_id)
    if preview and (preview.base_key != base.key or preview.source_key != source_key):
        preview = None
    return AdoptionContext(
        base_version_id=base.detail.version.id if base.detail else None,
        base_key=base.key,
        source_key=source_key,
        blocker=base.blocker,
        preview=preview,
    )


@router.post(
    "/branches/{branch_id}/studio/runs/{run_id}/nodes/{node_id}/adoption",
    response_model=AdoptionPreview,
)
async def preview_adoption(
    branch_id: str,
    run_id: str,
    node_id: str,
    payload: AdoptionInput,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
    settings: Settings = Depends(get_settings),
):
    from app.services.studio_adoption import adoption_base, create_preview, source_artifact

    await branch_exists(store, branch_id)
    async with branch_guard(branch_id):
        run, artifact, source_key = await source_artifact(store, branch_id, run_id, node_id)
        base = await adoption_base(store, graph, branch_id)
        if base.blocker:
            raise ConflictError(base.blocker)
        if base.key != payload.base_key or source_key != payload.source_key:
            raise ConflictError("正式方案或来源成稿已变化，请重新载入采用信息后生成预览。")
        if any(
            v.trigger == "studio_adopt:" + payload.request_id
            for v in await store.list_versions(branch_id)
        ):
            raise ConflictError("本次采用请求已完成，请重新载入正式行程。")
        existing = await store.workbench.adoption(branch_id, request_id=payload.request_id)
        if existing:
            if (existing.run_id, existing.node_id, existing.base_key, existing.source_key) != (
                run_id,
                node_id,
                base.key,
                source_key,
            ):
                raise ConflictError("同一次采用预览的来源或基线已变化，请重新创建预览。")
            return existing
        try:
            async with asyncio.timeout(210):
                preview = await create_preview(
                    base,
                    run,
                    node_id,
                    artifact,
                    source_key,
                    payload,
                    create_llm_provider(settings) if artifact.plan is None else None,
                    settings,
                )
        except TimeoutError as exc:
            raise LLMTimeout("整理采用预览超时，原方案未修改，可稍后重试。") from exc
        await store.workbench.save_adoption(preview)
        return preview


async def matched_adoption(
    store: SessionStore, branch_id: str, run_id: str, node_id: str, request_id: str
) -> AdoptionPreview:
    preview = await store.workbench.adoption(branch_id, request_id=request_id)
    if not preview or (preview.run_id, preview.node_id) != (run_id, node_id):
        raise InvalidRequest("未找到此成稿的采用预览，请重新生成。")
    return preview


@router.delete(
    "/branches/{branch_id}/studio/runs/{run_id}/nodes/{node_id}/adoption/{request_id}",
    status_code=204,
)
async def discard_adoption(
    branch_id: str,
    run_id: str,
    node_id: str,
    request_id: str,
    store: SessionStore = Depends(get_store),
):
    await branch_exists(store, branch_id)
    async with branch_guard(branch_id):
        existing = await store.workbench.adoption(branch_id, request_id=request_id)
        if existing and (existing.run_id, existing.node_id) != (run_id, node_id):
            raise ConflictError("采用预览不属于当前成稿。")
        # 已采用预览保留为回执，回复丢失后仍可准确重试。
        if not any(
            v.trigger == "studio_adopt:" + request_id for v in await store.list_versions(branch_id)
        ):
            await store.workbench.discard_adoption(branch_id, request_id)


@router.post(
    "/branches/{branch_id}/studio/runs/{run_id}/nodes/{node_id}/adoption/{request_id}/apply",
    response_model=VersionDetail,
)
async def apply_adoption(
    branch_id: str,
    run_id: str,
    node_id: str,
    request_id: str,
    store: SessionStore = Depends(get_store),
    graph=Depends(get_graph),
):
    from app.services.studio_adoption import commit_preview, source_artifact

    await branch_exists(store, branch_id)
    async with branch_guard(branch_id):
        preview = await matched_adoption(store, branch_id, run_id, node_id, request_id)
        applied = next(
            (
                v
                for v in await store.list_versions(branch_id)
                if v.trigger == "studio_adopt:" + request_id
            ),
            None,
        )
        if applied:
            return await store.get_version_detail(applied.id)
        _, _, source_key = await source_artifact(store, branch_id, run_id, node_id)
        if source_key != preview.source_key:
            raise ConflictError("来源成稿已变化，请重新生成采用预览。")
        return await commit_preview(store, graph, preview)
