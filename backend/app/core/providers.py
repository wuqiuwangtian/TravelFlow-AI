"""Provider 注册表 —— 大模型与检索服务的元数据。

**核心原则**（见规划 §19.8「不缓存易变信息」）：
本文件**只登记稳定的信息** —— 名称、官方文档链接、默认接口地址、是否需要密钥。
**绝不登记**免费额度、价格、优惠政策等会随时变动的第三方信息。

新增一个 Provider 只需要在下方列表里加一条，不需要改动其他代码。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    """一个外部服务的静态元数据。"""

    id: str
    name: str
    docs_url: str
    requires_api_key: bool = True
    default_base_url: str | None = None
    note: str = ""


# --------------------------------------------------------------------------
# 大模型服务商
# --------------------------------------------------------------------------
LLM_PROVIDERS: tuple[ProviderInfo, ...] = (
    ProviderInfo(
        "deepseek",
        "DeepSeek",
        "https://platform.deepseek.com/",
        default_base_url="https://api.deepseek.com/v1",
    ),
    ProviderInfo(
        "openai",
        "OpenAI",
        "https://platform.openai.com/docs",
        default_base_url="https://api.openai.com/v1",
    ),
    ProviderInfo(
        "moonshot",
        "月之暗面 Kimi",
        "https://platform.moonshot.cn/docs",
        default_base_url="https://api.moonshot.cn/v1",
    ),
    ProviderInfo(
        "zhipu",
        "智谱 GLM",
        "https://docs.bigmodel.cn/",
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
    ),
    ProviderInfo(
        "dashscope",
        "通义千问",
        "https://help.aliyun.com/zh/model-studio/",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    ProviderInfo(
        "siliconflow",
        "硅基流动",
        "https://docs.siliconflow.cn/",
        default_base_url="https://api.siliconflow.cn/v1",
    ),
    # ---- 本地部署：不需要 API Key ----
    ProviderInfo(
        "ollama",
        "本地 Ollama",
        "https://docs.ollama.com/",
        requires_api_key=False,
        default_base_url="http://localhost:11434/v1",
        note="完全本地运行，无需联网",
    ),
    ProviderInfo(
        "vllm",
        "本地 vLLM",
        "https://docs.vllm.ai/",
        requires_api_key=False,
        default_base_url="http://localhost:8000/v1",
        note="需要自行部署 vLLM 服务",
    ),
    ProviderInfo(
        "lmstudio",
        "本地 LM Studio",
        "https://lmstudio.ai/docs",
        requires_api_key=False,
        default_base_url="http://localhost:1234/v1",
        note="桌面端图形界面，适合新手",
    ),
    ProviderInfo(
        "custom",
        "自定义（OpenAI 兼容）",
        "https://platform.openai.com/docs/api-reference",
        requires_api_key=False,
        note="任何兼容 OpenAI 协议的服务",
    ),
)

# --------------------------------------------------------------------------
# 检索服务
# --------------------------------------------------------------------------
SEARCH_PROVIDERS: tuple[ProviderInfo, ...] = (
    ProviderInfo(
        "deepseek",
        "DeepSeek 网页搜索",
        "https://api-docs.deepseek.com/zh-cn/guides/search/",
        default_base_url="https://api.deepseek.com/v1",
        note="独立网页搜索接口，可复用 DeepSeek 官方模型 Key；按次计费",
    ),
    ProviderInfo(
        "tavily",
        "Tavily",
        "https://docs.tavily.com/",
        default_base_url="https://api.tavily.com",
        note="专为 LLM 设计，结果结构化程度高",
    ),
    ProviderInfo(
        "bocha",
        "博查 BochaAI",
        "https://open.bochaai.com/",
        default_base_url="https://api.bochaai.com",
    ),
    ProviderInfo(
        "qianfan",
        "百度智能云千帆",
        "https://cloud.baidu.com/doc/qianfan/index.html",
        note="国内服务，中文检索质量好",
    ),
    ProviderInfo(
        "searxng",
        "自建 SearXNG",
        "https://docs.searxng.org/",
        requires_api_key=False,
        note="需自行部署，检索完全自控",
    ),
    ProviderInfo(
        "ddg",
        "DuckDuckGo",
        "https://duckduckgo.com/",
        requires_api_key=False,
        note="无需任何密钥，可零配置开始",
    ),
)


# --------------------------------------------------------------------------
# 查询辅助
# --------------------------------------------------------------------------
def find_llm_provider(provider_id: str) -> ProviderInfo | None:
    return next((p for p in LLM_PROVIDERS if p.id == provider_id), None)


def find_search_provider(provider_id: str) -> ProviderInfo | None:
    return next((p for p in SEARCH_PROVIDERS if p.id == provider_id), None)


def is_local_llm(provider_id: str) -> bool:
    """本地部署的模型服务不需要 API Key。"""
    provider = find_llm_provider(provider_id)
    return provider is not None and not provider.requires_api_key
