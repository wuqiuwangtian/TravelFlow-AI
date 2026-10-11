"""FastAPI 依赖注入。

应用级资源（数据库连接、编排图、会话存储）在 ``lifespan`` 中构建并挂到
``app.state``，这里只提供**取用**函数 —— 接口层因此不必关心它们的创建过程。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.core.errors import ConfigError
from app.storage.session_store import SessionStore


def get_store(request: Request) -> SessionStore:
    """获取会话存储。"""
    return request.app.state.store


def get_graph(request: Request) -> Any:
    """获取已编译并带持久化的编排图。

    未完成模型 / 检索配置时给出**可操作**的提示，而不是抛 500。
    """
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        raise ConfigError(
            "尚未完成大模型或检索服务的配置，请先在设置中填写 API Key（或改用本地模型）"
        )
    return graph
