"""对外数据契约（DTO）。

只放**接口层**用到的模型；LLM / 检索 / 导出的内部模型留到各自里程碑定义。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

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


#: 设置项的长度上限（防止把 .env 写成巨无霸）
SETTING_MAX_LEN = 512

#: 禁止出现在设置值里的字符：换行/回车会让 .env 多出一行（配置注入）
_FORBIDDEN_CHARS = ("\n", "\r", "\x00")


def _validate_setting_value(value: str | None) -> str | None:
    """设置值的统一校验：无换行、无控制字符、长度可控。

    这是**接口层**的防线；``env_writer`` 里还有一道（直接调用它时也安全）。
    """
    if value is None:
        return None
    if any(ch in value for ch in _FORBIDDEN_CHARS):
        raise ValueError("不能包含换行符或空字符")
    if any(ord(ch) < 0x20 for ch in value):
        raise ValueError("不能包含控制字符")
    if len(value) > SETTING_MAX_LEN:
        raise ValueError(f"长度不能超过 {SETTING_MAX_LEN} 个字符")
    return value


def _validate_base_url(value: str | None) -> str | None:
    """校验接口地址。

    为什么要管：模型与检索的请求都会带上**用户的 API Key**。
    如果把 base_url 改成任意主机，Key 就会被送到那台服务器上
    —— 这是一条真实存在的密钥外送路径（尤其在本机无鉴权的前提下）。

    因此只允许 https，或显式的本机地址（本地模型场景）。
    """
    value = _validate_setting_value(value)
    if not value:
        return value

    from urllib.parse import urlparse

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("接口地址必须以 http:// 或 https:// 开头")
    if not parsed.hostname:
        raise ValueError("接口地址缺少主机名")

    if parsed.scheme == "http" and parsed.hostname not in _LOCAL_HOSTS:
        raise ValueError("非本机地址必须使用 https，否则 API Key 会以明文传输")
    return value


#: 允许使用 http 的本机主机名（本地模型服务）
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "0.0.0.0"}


class SettingsUpdate(BaseModel):
    """可写回 ``.env`` 的配置项（白名单，其余字段不可改）。

    **每个字段都必须通过值校验** —— 早期版本是裸 ``str``，
    导致 ``{"llm_model": "x\\nAPP_HOST=0.0.0.0"}`` 能注入额外配置行。
    """

    llm_provider: str | None = Field(default=None, max_length=64)
    llm_model: str | None = Field(default=None, max_length=SETTING_MAX_LEN)
    llm_api_key: str | None = Field(default=None, max_length=SETTING_MAX_LEN)
    llm_base_url: str | None = Field(default=None, max_length=SETTING_MAX_LEN)
    search_provider: str | None = Field(default=None, max_length=64)
    search_api_key: str | None = Field(default=None, max_length=SETTING_MAX_LEN)
    amap_api_key: str | None = Field(default=None, max_length=SETTING_MAX_LEN)
    searxng_base_url: str | None = Field(default=None, max_length=SETTING_MAX_LEN)
    default_detail_level: str | None = Field(default=None, max_length=64)

    @field_validator("llm_base_url", "searxng_base_url", mode="after")
    @classmethod
    def _check_urls(cls, value: str | None) -> str | None:
        return _validate_base_url(value)

    @field_validator(
        "llm_provider",
        "llm_model",
        "llm_api_key",
        "search_provider",
        "search_api_key",
        "amap_api_key",
        "default_detail_level",
        mode="after",
    )
    @classmethod
    def _check_values(cls, value: str | None) -> str | None:
        return _validate_setting_value(value)


class SettingsUpdateResponse(BaseModel):
    saved: bool
    changed: list[str] = Field(default_factory=list)
    message: str
    # 保存后是否成功重建运行时（图）—— 用户不必再手动重启后端
    reloaded: bool = False
    runtime_error: str | None = None


class ModelListResponse(BaseModel):
    """可用模型列表（走服务商的 ``GET /models``）。"""

    models: list[str] = Field(default_factory=list)
    message: str = ""


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


class ChatMessageItem(BaseModel):
    role: str
    content: str


class ChatHistoryResponse(BaseModel):
    """对话历史（存在 LangGraph checkpoint 里）。"""

    messages: list[ChatMessageItem] = Field(default_factory=list)
    clarification: dict[str, str] | None = None


# ---------------------------------------------------------------- 对话
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000, description="用户消息")
    session_id: str = Field(min_length=1)
    branch_id: str | None = Field(default=None, description="不传则使用会话主分支")
    detail_level: DetailLevel | None = Field(default=None, description="本次生成的详细度档位")
    request_id: str | None = Field(
        default=None, min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$"
    )


class ChatResponse(BaseModel):
    session_id: str
    branch_id: str
    version_id: str | None = None
    status: str
    reply: str
    plan: str = ""
    missing_fields: list[str] = Field(default_factory=list)
    clarification: dict[str, str] | None = None
