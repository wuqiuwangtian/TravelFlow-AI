"""统一异常体系与异常处理器。

三条约定：
1. ``message`` **面向用户**，必须说人话（对照阶段①缺陷 P2-2）；
2. 技术细节只写日志，**不进响应体**；
3. 每个异常都有稳定的 ``code``，前端可据此做差异化提示。

错误码与规划文档 §9.2 严格一致。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, get_trace_id

logger = get_logger(__name__)


# ---------------------------------------------------------------- 基类
class AppError(Exception):
    """所有业务异常的基类。"""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500
    default_message: str = "服务内部错误，请稍后重试"

    def __init__(self, message: str | None = None, *, detail: str | None = None) -> None:
        self.message = message or self.default_message
        self.detail = detail  # 仅用于日志
        super().__init__(self.message)


# ---------------------------------------------------------------- 配置
class ConfigError(AppError):
    code = "CONFIG_INVALID"
    http_status = 500
    default_message = "配置有误，请检查 .env 文件"


# ---------------------------------------------------------------- 请求
class InvalidRequest(AppError):
    code = "INVALID_REQUEST"
    http_status = 400
    default_message = "请求参数有误，请检查输入"


# ---------------------------------------------------------------- 资源
class NotFoundError(AppError):
    code = "NOT_FOUND"
    http_status = 404
    default_message = "请求的资源不存在"


class SessionNotFound(NotFoundError):
    code = "SESSION_NOT_FOUND"
    default_message = "会话不存在或已被删除"


class BranchNotFound(NotFoundError):
    code = "BRANCH_NOT_FOUND"
    default_message = "分支不存在或已被删除"


class ConflictError(AppError):
    code = "CONFLICT"
    http_status = 409
    default_message = "操作冲突，请刷新后重试"


class BranchConflict(ConflictError):
    code = "BRANCH_CONFLICT"
    default_message = "该分支正在被其他操作使用"


# ---------------------------------------------------------------- 外部服务
class ProviderError(AppError):
    code = "PROVIDER_ERROR"
    http_status = 502
    default_message = "外部服务暂时不可用，请稍后重试"


class LLMUnavailable(ProviderError):
    code = "LLM_UNAVAILABLE"
    default_message = "模型服务连不上，请检查配置与网络"


class LLMTimeout(ProviderError):
    code = "LLM_TIMEOUT"
    http_status = 504
    default_message = "模型响应超时，请稍后重试"


class LLMOutputInvalid(ProviderError):
    code = "LLM_OUTPUT_INVALID"
    http_status = 422
    default_message = "模型返回的内容格式异常，请重试或更换模型"


class SearchUnavailable(ProviderError):
    code = "SEARCH_PROVIDER_UNAVAILABLE"
    default_message = "检索服务暂时不可用，请检查网络或稍后重试"


# ---------------------------------------------------------------- 响应与处理器
def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    """构造统一错误响应体。"""
    return {"error": {"code": code, "message": message, "trace_id": get_trace_id()}}


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器 —— 保证**任何**异常都返回统一格式。"""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "业务异常: %s",
            exc.message,
            extra={"extra_fields": {"code": exc.code, "path": request.url.path}},
        )
        return JSONResponse(status_code=exc.http_status, content=error_body(exc.code, exc.message))

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """把框架层异常（404 等）也纳入统一错误格式。"""
        if exc.status_code == 404:
            code, message = "NOT_FOUND", "接口不存在"
        elif exc.status_code == 405:
            code, message = "METHOD_NOT_ALLOWED", "该接口不支持此请求方法"
        else:
            code, message = "HTTP_ERROR", str(exc.detail)
        return JSONResponse(status_code=exc.status_code, content=error_body(code, message))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.info(
            "参数校验失败",
            extra={"extra_fields": {"path": request.url.path, "errors": len(exc.errors())}},
        )
        return JSONResponse(
            status_code=400,
            content=error_body("INVALID_REQUEST", "请求参数有误，请检查输入"),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未捕获异常: %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "服务内部错误，请稍后重试"),
        )
