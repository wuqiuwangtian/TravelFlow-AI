"""对外数据契约（DTO）。

只放**接口层**用到的模型；LLM / 检索 / 导出的内部模型留到各自里程碑定义。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.enums import DetailLevel
from app.domain.models import BranchInfo, SessionInfo, VersionInfo


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


# ---------------------------------------------------------------- 会话 / 分支 / 版本
class CreateSessionRequest(BaseModel):
    title: str = Field(default="新的旅行", min_length=1, max_length=100)


class UpdateSessionRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    archived: bool | None = None


class SessionListResponse(BaseModel):
    items: list[SessionInfo]
    total: int


class SessionDetailResponse(BaseModel):
    session: SessionInfo
    branches: list[BranchInfo]
    versions: dict[str, list[VersionInfo]] = Field(default_factory=dict)


class CreateBranchRequest(BaseModel):
    """从某个版本分叉。

    - 只填 ``parent_branch_id`` 表示从该分支最新状态分叉；
    - 填 ``fork_version_id`` 表示从指定历史版本分叉（「回到初版」场景）。
    """

    title: str = Field(default="新分支", min_length=1, max_length=50)
    parent_branch_id: str | None = None
    fork_version_id: str | None = None


class BranchListResponse(BaseModel):
    items: list[BranchInfo]


class VersionListResponse(BaseModel):
    items: list[VersionInfo]


# ---------------------------------------------------------------- 对话
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000, description="用户消息")
    session_id: str = Field(min_length=1)
    branch_id: str | None = Field(default=None, description="不传则使用会话主分支")
    detail_level: DetailLevel | None = Field(default=None, description="本次生成的详细度档位")


class ChatResponse(BaseModel):
    session_id: str
    branch_id: str
    version_id: str | None = None
    status: str
    reply: str
    plan: str = ""
    missing_fields: list[str] = Field(default_factory=list)
