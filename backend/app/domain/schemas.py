"""对外数据契约（DTO）。

只放**接口层**用到的模型；LLM / 检索 / 导出的内部模型留到各自里程碑定义。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- 系统
class HealthResponse(BaseModel):
    status: str = "ok"
    name: str
    version: str
    uptime_seconds: float


class ProviderItem(BaseModel):
    """服务商条目 —— **只含稳定信息**，不含额度/价格（见规划 §19.8）。"""

    id: str
    name: str
    docs_url: str
    requires_api_key: bool
    default_base_url: str | None = None
    note: str = ""


class ProvidersResponse(BaseModel):
    llm: list[ProviderItem]
    search: list[ProviderItem]


class LLMConfigCheck(BaseModel):
    provider: str
    model: str
    base_url: str | None
    has_key: bool
    ready: bool


class SearchConfigCheck(BaseModel):
    provider: str
    has_key: bool
    ready: bool


class ConfigCheckResponse(BaseModel):
    llm: LLMConfigCheck
    search: SearchConfigCheck
    default_detail_level: str
    warnings: list[str] = Field(default_factory=list)


class ConnectionTestResponse(BaseModel):
    """连接测试结果。

    始终返回 HTTP 200，用 ``ok`` 字段表达成败 —— 设置页的「测试连接」
    按钮据此直接渲染结果，无需处理异常分支。
    """

    ok: bool
    provider: str
    message: str


# ---------------------------------------------------------------- 错误
class ErrorDetail(BaseModel):
    code: str
    message: str
    trace_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
