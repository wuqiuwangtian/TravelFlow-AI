"""流式事件的发送工具。

**为什么需要这一层**：同一份节点代码要能同时服务两种调用方式 ——
一次性返回（`POST /api/chat`）与流式返回（`POST /api/chat/stream`）。
``get_stream_writer()`` 只在流式调用时存在，直接调用会在非流式场景抛错，
所以统一在这里包一层「有就发，没有就算了」。

事件类型（前端按 ``type`` 分派）：

| type | 含义 | 字段 |
| :--- | :--- | :--- |
| ``progress`` | 阶段进度 | ``stage`` / ``message`` |
| ``token`` | 增量文本 | ``text`` |
| ``notice`` | 提示（如检索部分失败） | ``message`` |
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 各阶段对用户展示的文案（用「人话」而不是节点名）
STAGE_MESSAGES: dict[str, str] = {
    "collect": "正在理解你的需求…",
    "research": "正在检索航班、住宿与景点信息…",
    "plan": "正在为你撰写路书…",
    "refine": "正在按你的要求调整…",
}


def emit(event: dict[str, Any]) -> None:
    """向流式通道推送一个事件；非流式调用时静默忽略。"""
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:
        # 非流式调用：没有 writer，正常情况，不需要关心
        return

    try:
        writer(event)
    except Exception as exc:  # pragma: no cover - 防御性
        logger.debug("推送流式事件失败: %s", exc)


def emit_progress(stage: str, message: str | None = None) -> None:
    emit({"type": "progress", "stage": stage, "message": message or STAGE_MESSAGES.get(stage, "")})


def emit_token(text: str) -> None:
    if text:
        emit({"type": "token", "text": text})


def emit_notice(message: str) -> None:
    emit({"type": "notice", "message": message})


#: 调用方通过 config.configurable 传进来的开关名
STREAM_FLAG = "stream_llm"


def is_streaming() -> bool:
    """当前是否处于流式调用中（供节点决定用流式还是结构化生成）。

    **为什么不用「能否拿到 stream writer」来判断**：
    ``get_stream_writer()`` 在非流式调用时**不会报错**（返回空实现），
    据此判断会让普通请求也走流式分支 —— 结果是路书内容被换成了
    流式接口的返回值。实测踩过这个坑，所以改为由调用方**显式声明**。
    """
    try:
        from langgraph.config import get_config

        config = get_config()
    except Exception:
        return False

    configurable = (config or {}).get("configurable") or {}
    return bool(configurable.get(STREAM_FLAG))
