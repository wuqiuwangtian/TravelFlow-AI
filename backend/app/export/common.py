"""导出层公共逻辑：引用链接化、免责声明、待办事项与官方渠道配对。

**这里是「防幻觉」闭环的最后一环**：
大模型只写了 ``[1]`` 这样的编号，真实 URL 由程序在此处填回（规划 §19.3）。
"""

from __future__ import annotations

import re
from datetime import datetime

from app.core.channels import VerificationChannel, channel_for
from app.core.logging import get_logger
from app.domain.models import Citation, TodoItem

logger = get_logger(__name__)

_CITATION_PATTERN = re.compile(r"\[(\d+)\]")

DISCLAIMER_HEAD = (
    "> ⚠️ 本路书由 AI 基于 {when} 的联网检索结果生成，**仅供参考**。\n"
    "> 票价、开放时间、预约政策等信息可能随时变动，出行前请通过下方【官方渠道】核实。"
)


def format_when(searched_at: datetime | None) -> str:
    return searched_at.strftime("%Y-%m-%d %H:%M") if searched_at else "未知时间"


def disclaimer_text(searched_at: datetime | None) -> str:
    return DISCLAIMER_HEAD.format(when=format_when(searched_at))


def linkify_citations(markdown: str, citations: list[Citation]) -> tuple[str, list[int]]:
    """把正文里的 ``[n]`` 替换为指向真实 URL 的链接。

    返回 ``(新文本, 未命中的编号列表)``。
    未命中的编号会**保留原样并记录警告** —— 这本身是一个有价值的信号：
    说明大模型引用了不存在的来源（编造引用）。
    """
    index_map = {c.index: c for c in citations}
    missing: list[int] = []

    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        citation = index_map.get(index)
        if citation is None:
            missing.append(index)
            return match.group(0)
        return f"[[{index}]]({citation.url})"

    linked = _CITATION_PATTERN.sub(replace, markdown)
    if missing:
        logger.warning(
            "路书引用了不存在的编号（疑似编造）",
            extra={"extra_fields": {"missing": sorted(set(missing))}},
        )
    return linked, missing


def build_todos(todos: list[TodoItem]) -> list[dict[str, object]]:
    """把待办事项与官方渠道配对，供模板渲染。

    大模型只说了「要办什么」，「去哪办」由 ``channel_for`` 决定 ——
    模型从不接触办理链接（规划 §19.5）。
    """
    items: list[dict[str, object]] = []
    for todo in todos:
        channel: VerificationChannel = channel_for(str(todo.kind))
        items.append(
            {
                "what": todo.what,
                "when": todo.when,
                "note": todo.note,
                "channel": channel,
            }
        )
    return items


def reference_lines(citations: list[Citation]) -> list[str]:
    """渲染 Markdown 参考资料列表。"""
    lines: list[str] = []
    for citation in citations:
        when = citation.retrieved_at.strftime("%Y-%m-%d %H:%M")
        lines.append(
            f"{citation.index}. [{citation.title}]({citation.url})"
            f"  \n   <sub>{citation.provider} · 抓取于 {when}</sub>"
        )
    return lines
