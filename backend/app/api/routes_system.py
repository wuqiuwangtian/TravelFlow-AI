"""系统接口 —— 健康检查、配置自检、服务商列表。

设计约束：
- **只读、无副作用**；
- **不依赖任何外部服务** —— 健康检查必须永远能答（否则 Docker healthcheck 会误判）；
- 服务商列表只返回名称与官方文档链接，**不含任何额度/价格信息**（规划 §19.8）。
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.providers import LLM_PROVIDERS, SEARCH_PROVIDERS, ProviderInfo
from app.domain.schemas import (
    ConfigCheckResponse,
    HealthResponse,
    LLMConfigCheck,
    ProviderItem,
    ProvidersResponse,
    SearchConfigCheck,
)

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
