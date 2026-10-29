"""应用配置 —— 全部配置项的单一来源。

设计要点：
1. 使用 ``pydantic-settings``，启动时即完成类型与取值校验；
2. 缺失关键配置**不阻止启动**（小白可能先启动再配置），而是产出「人话」警告；
3. 密钥永不出现在任何响应中，对外只暴露 ``has_key`` 布尔值。

对应规划文档：``docs/02-upgrade-plan.md`` §12 配置设计。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.providers import find_llm_provider, find_search_provider, is_local_llm


class Settings(BaseSettings):
    """从环境变量与 ``.env`` 加载的配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- 服务 ----------------
    app_name: str = "TravelFlow AI"
    app_version: str = "1.0.0"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_log_level: str = "INFO"
    # 默认输出人类可读日志；设为 true 则输出 JSON 行（便于日志采集）
    app_log_json: bool = False
    # 跨域来源白名单。默认只放行本机前端（端口 3100）。
    app_cors_origins: str = "http://localhost:3100,http://127.0.0.1:3100"

    # 跨域严格模式（安全相关）：
    #   true（默认）= **只**放行上面 APP_CORS_ORIGINS 里列出的来源；
    #   false        = 额外放行任意本机来源（localhost/127.0.0.1 的任意端口）。
    #
    # 为什么默认严格：本地服务虽然只监听 127.0.0.1，但同机其它端口的页面
    # 若能跨域读取本 API，就可能读走会话数据、或消耗你的模型额度。
    #
    # 便利性如何保证：**启动脚本会按实际前端端口自动注入白名单**，
    # 因此你改 FRONTEND_PORT 不会出现「连不上后端」。
    # 手动启动时若改了端口，请同步改 APP_CORS_ORIGINS。
    app_cors_strict: bool = True

    # ---------------- 数据 ----------------
    session_db_path: Path = Path("./data/travelflow.db")
    session_ttl_hours: int = 168

    # 配置文件路径；留空则使用 backend/.env
    # （可配置是为了让测试能指向临时文件，避免污染真实配置）
    app_env_file: Path | None = None

    # ---------------- 大模型 ----------------
    llm_provider: str = "deepseek"
    # DeepSeek 的模型名会随官方调整（如 deepseek-flash / deepseek-v4-pro），
    # 因此设置页提供「获取可用模型」按钮（走 /v1/models），不必手写。
    llm_model: str = "deepseek-flash"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_temperature: float = 0.7
    llm_timeout_seconds: int = 90
    llm_max_retries: int = 2

    # ---------------- 信息检索 ----------------
    search_provider: str = "tavily"
    search_api_key: str | None = None
    search_max_results: int = 5
    search_timeout_seconds: int = 20
    searxng_base_url: str | None = None

    # ---------------- 地图（选填）----------------
    amap_api_key: str | None = None
    amap_timeout_seconds: int = 12

    # ---------------- 生成偏好 ----------------
    default_detail_level: str = "L2_standard"

    # ---------------- 派生属性 ----------------

    @property
    def cors_origin_list(self) -> list[str]:
        """把逗号分隔的来源串解析成列表。"""
        return [o.strip() for o in self.app_cors_origins.split(",") if o.strip()]

    @property
    def env_file_path(self) -> Path:
        """实际使用的 ``.env`` 路径（本文件位于 ``backend/app/core/``）。"""
        if self.app_env_file:
            return Path(self.app_env_file)
        return Path(__file__).resolve().parents[2] / ".env"

    @property
    def resolved_llm_base_url(self) -> str | None:
        """用户未显式填写时，回落到 Provider 预设地址。"""
        if self.llm_base_url:
            return self.llm_base_url
        provider = find_llm_provider(self.llm_provider)
        return provider.default_base_url if provider else None

    @property
    def llm_ready(self) -> bool:
        """LLM 是否已具备调用条件。"""
        if is_local_llm(self.llm_provider):
            return True  # 本地模型无需 Key
        return bool(self.llm_api_key)

    @property
    def resolved_search_api_key(self) -> str | None:
        """检索专用 Key 优先；仅允许复用 DeepSeek 官方接口的模型 Key。"""
        if self.search_api_key:
            return self.search_api_key
        if self.search_provider != "deepseek" or self.llm_provider != "deepseek":
            return None
        try:
            url = urlsplit(self.resolved_llm_base_url or "")
            official = (
                url.scheme == "https"
                and url.hostname == "api.deepseek.com"
                and url.port in {None, 443}
                and not url.username
                and not url.password
            )
        except ValueError:
            return None
        return self.llm_api_key if official else None

    @property
    def search_ready(self) -> bool:
        """检索服务是否已具备调用条件。"""
        provider = find_search_provider(self.search_provider)
        if provider is None:
            return False
        if not provider.requires_api_key:
            return True  # 例如 DuckDuckGo、自建 SearXNG
        if self.search_provider == "searxng":
            return bool(self.searxng_base_url)
        return bool(self.resolved_search_api_key)

    def startup_warnings(self) -> list[str]:
        """启动自检警告 —— 面向用户，必须是「人话 + 可操作」。"""
        warnings: list[str] = []

        if find_llm_provider(self.llm_provider) is None:
            known = "、".join(p.id for p in _all_llm_ids())
            warnings.append(f"未知的模型服务商「{self.llm_provider}」，可选：{known}")
        elif not self.llm_ready:
            warnings.append(
                f"未配置 LLM_API_KEY，请在 .env 中填写（服务商：{self.llm_provider}），"
                "或改用本地模型（LLM_PROVIDER=ollama，需先启动 Ollama）"
            )

        if find_search_provider(self.search_provider) is None:
            warnings.append(f"未知的检索服务「{self.search_provider}」")
        elif not self.search_ready:
            if self.search_provider == "searxng":
                warnings.append("未配置 SEARXNG_BASE_URL，自建 SearXNG 需要填写其地址")
            elif self.search_provider == "deepseek":
                warnings.append(
                    "DeepSeek 网页搜索需要 SEARCH_API_KEY，"
                    "或已配置 DeepSeek 官方模型接口的 LLM_API_KEY"
                )
            else:
                warnings.append(
                    f"未配置 SEARCH_API_KEY（检索服务：{self.search_provider}），"
                    "可改用 SEARCH_PROVIDER=ddg 免密钥检索"
                )

        # 安全提醒：把服务暴露到本机之外会显著放大风险
        if self.app_host not in {"127.0.0.1", "localhost", "::1"}:
            warnings.append(
                f"APP_HOST={self.app_host} 表示服务将对本机之外开放，"
                "任何能访问该端口的人都可以读取你的会话与路书。"
                "如非必要，请改回 127.0.0.1"
            )

        if not self.app_cors_strict:
            warnings.append(
                "跨域当前为宽松模式（放行任意本机端口）。"
                "如需只允许白名单来源，请设置 APP_CORS_STRICT=true"
            )
        elif not self.cors_origin_list:
            warnings.append("APP_CORS_ORIGINS 为空，前端将无法调用本接口")

        return warnings


def _all_llm_ids():
    from app.core.providers import LLM_PROVIDERS

    return LLM_PROVIDERS


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局配置单例（便于测试时通过 ``cache_clear`` 重置）。"""
    return Settings()
