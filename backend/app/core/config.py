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

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.providers import find_llm_provider, find_search_provider, is_local_llm

# 本地部署的模型服务不需要 API Key（Ollama / vLLM / LM Studio 等）
_API_KEY_OPTIONAL_LLM = {"ollama", "vllm", "lmstudio", "custom"}


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
    app_version: str = "0.1.0"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_log_level: str = "INFO"
    # 默认输出人类可读日志；设为 true 则输出 JSON 行（便于日志采集）
    app_log_json: bool = False
    # 默认不使用 "*"，对照阶段①缺陷 P2-1
    app_cors_origins: str = "http://localhost:3000"

    # ---------------- 数据 ----------------
    session_db_path: Path = Path("./data/travelflow.db")
    session_ttl_hours: int = 168

    # ---------------- 大模型 ----------------
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-chat"
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

    # ---------------- 生成偏好 ----------------
    default_detail_level: str = "L2_standard"

    # ---------------- 派生属性 ----------------

    @property
    def cors_origin_list(self) -> list[str]:
        """把逗号分隔的来源串解析成列表。"""
        return [o.strip() for o in self.app_cors_origins.split(",") if o.strip()]

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
    def search_ready(self) -> bool:
        """检索服务是否已具备调用条件。"""
        provider = find_search_provider(self.search_provider)
        if provider is None:
            return False
        if not provider.requires_api_key:
            return True  # 例如 DuckDuckGo、自建 SearXNG
        if self.search_provider == "searxng":
            return bool(self.searxng_base_url)
        return bool(self.search_api_key)

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
            else:
                warnings.append(
                    f"未配置 SEARCH_API_KEY（检索服务：{self.search_provider}），"
                    "可改用 SEARCH_PROVIDER=ddg 免密钥检索"
                )

        return warnings


def _all_llm_ids():
    from app.core.providers import LLM_PROVIDERS

    return LLM_PROVIDERS


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局配置单例（便于测试时通过 ``cache_clear`` 重置）。"""
    return Settings()
