"""系统接口 —— 健康检查、配置自检、服务商列表、设置读写。

设计约束：
- 健康检查**不依赖任何外部服务**（否则 Docker healthcheck 会误判）；
- 系统类接口在**未配置任何 Key** 时也必须可用（否则用户打不开设置页）；
- 服务商列表只返回名称与官方文档链接，**不含任何额度/价格信息**（规划 §19.8）。
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from app.core.config import Settings, get_settings
from app.core.env_writer import FIELD_TO_ENV, write_env
from app.core.errors import ConfigError
from app.core.providers import LLM_PROVIDERS, SEARCH_PROVIDERS, ProviderInfo
from app.core.runtime import apply_runtime, build_runtime
from app.domain.schemas import (
    ConfigCheckResponse,
    ConnectionTestResponse,
    HealthResponse,
    LLMConfigCheck,
    ModelListResponse,
    ProviderItem,
    ProvidersResponse,
    SearchConfigCheck,
    SettingsUpdate,
    SettingsUpdateResponse,
)
from app.llm.factory import create_llm_provider
from app.search.factory import create_search_provider

router = APIRouter(prefix="/api/system", tags=["system"])

# 进程启动时刻，用于计算运行时长
_STARTED_AT = time.monotonic()


def _to_item(provider: ProviderInfo) -> ProviderItem:
    return ProviderItem(
        id=provider.id,
        name=provider.name,
        docs_url=provider.docs_url,
        requires_api_key=provider.requires_api_key,
        default_base_url=provider.default_base_url,
        note=provider.note,
    )


@router.get("/health", response_model=HealthResponse, summary="健康检查")
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """存活探针：不触碰外部依赖，永远能快速返回。"""
    return HealthResponse(
        name=settings.app_name,
        version=settings.app_version,
        uptime_seconds=round(time.monotonic() - _STARTED_AT, 2),
    )


@router.get("/config", response_model=ConfigCheckResponse, summary="配置自检")
async def config_check(settings: Settings = Depends(get_settings)) -> ConfigCheckResponse:
    """返回当前配置状态。

    **安全约束**：密钥永不回显，只返回 ``has_key`` 布尔值。
    """
    return ConfigCheckResponse(
        llm=LLMConfigCheck(
            provider=settings.llm_provider,
            model=settings.llm_model,
            base_url=settings.resolved_llm_base_url,
            has_key=bool(settings.llm_api_key),
            ready=settings.llm_ready,
        ),
        search=SearchConfigCheck(
            provider=settings.search_provider,
            has_key=bool(settings.search_api_key),
            ready=settings.search_ready,
        ),
        default_detail_level=settings.default_detail_level,
        warnings=settings.startup_warnings(),
    )


@router.get("/providers", response_model=ProvidersResponse, summary="可用服务商列表")
async def providers() -> ProvidersResponse:
    """列出支持的大模型与检索服务商，供设置页渲染下拉框。"""
    return ProvidersResponse(
        llm=[_to_item(p) for p in LLM_PROVIDERS],
        search=[_to_item(p) for p in SEARCH_PROVIDERS],
    )


@router.post("/test-llm", response_model=ConnectionTestResponse, summary="测试模型连接")
async def test_llm(settings: Settings = Depends(get_settings)) -> ConnectionTestResponse:
    """实际发一个极小请求验证模型连通性。

    配置有误时也返回 200，由 ``ok`` 字段表达结果，方便设置页直接渲染。
    """
    try:
        provider = create_llm_provider(settings)
    except ConfigError as exc:
        return ConnectionTestResponse(ok=False, provider=settings.llm_provider, message=exc.message)

    ok, message = await provider.health_check()
    return ConnectionTestResponse(ok=ok, provider=settings.llm_provider, message=message)


@router.post("/test-search", response_model=ConnectionTestResponse, summary="测试检索连接")
async def test_search(settings: Settings = Depends(get_settings)) -> ConnectionTestResponse:
    """实际发一次检索请求验证连通性。"""
    try:
        provider = create_search_provider(settings)
    except ConfigError as exc:
        return ConnectionTestResponse(
            ok=False, provider=settings.search_provider, message=exc.message
        )

    try:
        results = await provider.search("重庆 旅游", max_results=1)
    except Exception as exc:  # 领域异常已在 Provider 内统一包装
        message = getattr(exc, "message", str(exc))
        return ConnectionTestResponse(ok=False, provider=settings.search_provider, message=message)

    return ConnectionTestResponse(
        ok=True,
        provider=settings.search_provider,
        message=f"连接正常，返回 {len(results)} 条结果",
    )


# ============================================================ 设置读写
@router.get("/settings", summary="读取当前设置")
async def read_settings(settings: Settings = Depends(get_settings)) -> dict:
    """返回可编辑的配置项。

    **安全约束**：密钥永不回显，只返回 ``*_has_key`` 布尔值。
    """
    return {
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "llm_base_url": settings.llm_base_url or "",
        "llm_has_key": bool(settings.llm_api_key),
        "search_provider": settings.search_provider,
        "searxng_base_url": settings.searxng_base_url or "",
        "search_has_key": bool(settings.search_api_key),
        "amap_has_key": bool(settings.amap_api_key),
        "default_detail_level": settings.default_detail_level,
        "cors_strict": settings.app_cors_strict,
        "env_path": str(settings.env_file_path),
    }


@router.put("/settings", response_model=SettingsUpdateResponse, summary="保存设置")
async def update_settings(
    payload: SettingsUpdate,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> SettingsUpdateResponse:
    """把改动写回 ``.env``，并**立即重建运行时**。

    用户反馈：改完配置还要手动重启后端，那还不如直接改 ``.env`` ——
    那样界面配置就失去意义了。因此这里保存后直接重建编排图，做到**即时生效**。
    """
    updates: dict[str, str] = {}
    for field, env_key in FIELD_TO_ENV.items():
        value = getattr(payload, field)
        if value is not None:
            updates[env_key] = value

    if not updates:
        return SettingsUpdateResponse(saved=False, message="没有需要保存的改动")

    changed = write_env(settings.env_file_path, updates)
    if not changed:
        return SettingsUpdateResponse(saved=True, message="配置没有变化")

    # ---- 重建运行时（不必重启）----
    get_settings.cache_clear()
    fresh = get_settings()
    result = build_runtime(fresh, checkpointer=getattr(request.app.state, "checkpointer", None))
    apply_runtime(request.app, result)

    if result.ok:
        return SettingsUpdateResponse(
            saved=True,
            changed=changed,
            reloaded=True,
            message=f"已保存 {len(changed)} 项，配置已即时生效。",
        )

    return SettingsUpdateResponse(
        saved=True,
        changed=changed,
        reloaded=False,
        runtime_error=result.error,
        message=f"已保存 {len(changed)} 项，但服务尚未就绪：{result.error}",
    )


@router.get("/models", response_model=ModelListResponse, summary="获取可用模型列表")
async def list_models(settings: Settings = Depends(get_settings)) -> ModelListResponse:
    """向服务商查询当前可用模型。

    为什么需要：模型名会随官方调整（DeepSeek 就改过多次），
    让用户手写极易出错，而且名字写错时的报错往往难以理解。
    """
    try:
        provider = create_llm_provider(settings)
    except ConfigError as exc:
        return ModelListResponse(message=exc.message)

    if not hasattr(provider, "list_models"):
        return ModelListResponse(message="当前服务商不支持自动获取模型列表，请手动填写")

    try:
        models = await provider.list_models()
    except Exception as exc:
        return ModelListResponse(message=getattr(exc, "message", str(exc)))

    return ModelListResponse(models=models, message=f"共 {len(models)} 个可用模型")
