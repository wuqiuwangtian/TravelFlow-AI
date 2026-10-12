"""运行时装配：把配置变成可用的「图」。

抽出来的原因：**配置改了之后需要立即重建**，不能要求用户重启后端
（用户反馈：在前端改配置还要重启，还不如直接改 .env 文件方便）。

``build_runtime`` 同时被启动流程与「保存设置」接口复用，保证两条路径行为一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.graph import build_graph
from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.llm.factory import create_llm_provider
from app.search.factory import create_search_provider

logger = get_logger(__name__)


@dataclass(slots=True)
class RuntimeBuildResult:
    """构建结果：成功时 ``graph`` 非空，失败时 ``error`` 给出**人话**原因。"""

    graph: Any | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.graph is not None


def build_runtime(settings: Settings, *, checkpointer: Any) -> RuntimeBuildResult:
    """按当前配置构建编排图。

    **失败不抛异常** —— 未配置 Key 属于正常状态（前端仍要能打开设置页），
    因此这里把原因作为返回值交给调用方决定如何呈现。
    """
    try:
        llm = create_llm_provider(settings)
        search = create_search_provider(settings)
    except AppError as exc:
        return RuntimeBuildResult(error=exc.message)
    except Exception as exc:  # pragma: no cover - 防御性
        return RuntimeBuildResult(error=str(exc))

    try:
        graph = build_graph(llm, search, checkpointer=checkpointer)
    except Exception as exc:  # pragma: no cover - 防御性
        return RuntimeBuildResult(error=f"编排图构建失败：{exc}")

    logger.info("运行时已就绪: llm=%s search=%s", settings.llm_provider, settings.search_provider)
    return RuntimeBuildResult(graph=graph)


def apply_runtime(app: Any, result: RuntimeBuildResult) -> None:
    """把构建结果写入应用状态（供依赖注入读取）。"""
    app.state.graph = result.graph
    app.state.runtime_error = result.error
