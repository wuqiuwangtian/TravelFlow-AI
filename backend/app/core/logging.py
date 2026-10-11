"""结构化日志与请求追踪 ID。

- ``trace_id`` 通过 ContextVar 传递，由中间件在请求入口注入，全链路可追溯；
- 默认输出**人类可读**格式（小白友好），可通过 ``APP_LOG_JSON=true`` 切换为 JSON 行；
- 对照阶段①缺陷 P2-9：旧项目用 ``print`` 当日志，多会话并发时无法追溯。
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any

_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")


def new_trace_id() -> str:
    """生成一个短小、可读、便于在日志里肉眼比对的追踪 ID。"""
    return uuid.uuid4().hex[:12]


def get_trace_id() -> str:
    return _trace_id.get()


def set_trace_id(value: str) -> None:
    _trace_id.set(value)


class JsonFormatter(logging.Formatter):
    """JSON 行格式，便于日志采集。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": get_trace_id(),
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """开发/小白友好的可读格式。"""

    def format(self, record: logging.LogRecord) -> str:
        line = (
            f"{self.formatTime(record, '%H:%M:%S')} "
            f"{record.levelname:<7} [{get_trace_id()}] "
            f"{record.name} - {record.getMessage()}"
        )
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict) and extra:
            line += "  " + " ".join(f"{k}={v}" for k, v in extra.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(level: str = "INFO", *, json_output: bool = False) -> None:
    """初始化根日志器。重复调用是安全的（会先清空已有 handler）。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else HumanFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # 第三方库太吵，压到 WARNING
    for noisy in ("httpx", "httpcore", "uvicorn.access", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
