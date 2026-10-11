"""FastAPI 应用装配。

本模块**只做装配**：生命周期、中间件、异常处理、路由挂载。
不含任何业务逻辑 —— 对照阶段①缺陷：旧项目在 ``server.py`` 里写业务、建 LLM 客户端。

启动方式::

    cd backend && uv run uvicorn app.main:app --reload
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.agents.checkpoint import open_checkpointer
from app.agents.graph import build_graph
from app.api import routes_chat, routes_export, routes_session, routes_system
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import get_logger, new_trace_id, set_trace_id, setup_logging
from app.llm.factory import create_llm_provider
from app.search.factory import create_search_provider
from app.storage.database import create_connection
from app.storage.session_store import SessionStore

logger = get_logger(__name__)

# 本机来源：localhost / 127.0.0.1 / [::1]，端口任意
LOCAL_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动时准备资源，关闭时释放。

    **关键设计**：即使模型或检索尚未配置，服务也必须能启动 ——
    此时前端仍可打开设置页完成配置。因此编排图的构建失败只记警告，不阻断启动。
    """
    settings = get_settings()
    setup_logging(settings.app_log_level, json_output=settings.app_log_json)

    logger.info("%s v%s 启动中…", settings.app_name, settings.app_version)
    logger.info("配置加载完成: llm=%s search=%s", settings.llm_provider, settings.search_provider)
    for warning in settings.startup_warnings():
        logger.warning("配置提示: %s", warning)

    async with AsyncExitStack() as stack:
        # ---- 业务数据库 ----
        conn = await create_connection(settings.session_db_path)
        stack.push_async_callback(conn.close)
        app.state.db = conn
        app.state.store = SessionStore(conn)

        # ---- 编排图（依赖模型与检索配置）----
        app.state.graph = None
        try:
            llm = create_llm_provider(settings)
            search = create_search_provider(settings)
            checkpointer = await stack.enter_async_context(
                open_checkpointer(settings.session_db_path)
            )
            app.state.checkpointer = checkpointer
            app.state.graph = build_graph(llm, search, checkpointer=checkpointer)
            logger.info("编排图已就绪")
        except Exception as exc:  # 未配置 Key 属正常情况
            logger.warning("编排图未就绪（不影响启动，请完成配置后重启）: %s", exc)

        yield

    logger.info("服务已停止")


def create_app() -> FastAPI:
    """构建并返回 FastAPI 实例（工厂函数，便于测试中重复创建）。"""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="本地旅行助手 —— 多轮对话收集需求、联网检索真实信息、生成可执行路书",
        lifespan=lifespan,
    )

    # CORS
    #
    # 这是一个**本地工具**：页面可能跑在 3000 / 3100 / 5173 等任意端口上，
    # 用户改端口后如果忘了同步白名单，看到的现象是「前端连不上后端」，
    # 排查成本很高（浏览器不会告诉你真实原因是 CORS）。
    #
    # 默认（APP_CORS_STRICT=false）用正则**放行任意本机来源（不限端口）**；
    # 设为 true 则只放行 APP_CORS_ORIGINS 中列出的来源。
    #
    # 安全边界说明：本服务的真实边界是「只监听 127.0.0.1」（见 APP_HOST），
    # 该开关属于**纵深防御** —— 限制的是「本机其它端口的页面能否读取本 API」。
    # 需要说明的是它并非主要防线：任何能在本机执行代码的程序，本就可以直接读取
    # .env 与数据库文件。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=None if settings.app_cors_strict else LOCAL_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next) -> Response:
        """为每个请求注入 trace_id，并在响应头回传，便于排障。"""
        trace_id = request.headers.get("X-Trace-Id") or new_trace_id()
        set_trace_id(trace_id)

        started = time.perf_counter()
        response: Response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000

        response.headers["X-Trace-Id"] = trace_id
        logger.info(
            "%s %s -> %d",
            request.method,
            request.url.path,
            response.status_code,
            extra={"extra_fields": {"ms": round(elapsed_ms, 1)}},
        )
        return response

    register_exception_handlers(app)

    app.include_router(routes_system.router)
    app.include_router(routes_session.router)
    app.include_router(routes_chat.router)
    app.include_router(routes_export.router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        """根路径：给出可用的入口提示，而不是 404。"""
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "health": "/api/system/health",
        }

    return app


app = create_app()
