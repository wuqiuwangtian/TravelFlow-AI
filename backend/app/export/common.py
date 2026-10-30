"""导出层公共逻辑：引用链接化、免责声明、待办事项与官方渠道配对。

**这里是「防幻觉」闭环的最后一环**：
大模型只写了 ``[1]`` 这样的编号，真实 URL 由程序在此处填回（规划 §19.3）。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TypedDict
from urllib.parse import urlparse

from app.core.channels import VerificationChannel, channel_for
from app.core.logging import get_logger
from app.domain.models import Citation, TodoItem, VersionDetail

logger = get_logger(__name__)

_CITATION_PATTERN = re.compile(r"\[(\d+)\]")

#: 只允许这两种协议进入导出文件的链接。
#:
#: 为什么需要：检索结果的 URL 是**外部数据**，之前是原样透传进 ``href``。
#: 若某个来源返回 ``javascript:...`` 或 ``data:text/html,...``，
#: 导出的 HTML 里就会出现一条可点击的恶意链接。
#: （正文里的模型链接走 markdown-it，已由它自己的 validateLink 拦住；
#:   但模板里这两处 href 是绕过校验的，所以必须在这里筛一遍。）
SAFE_URL_SCHEMES = {"http", "https"}

DISCLAIMER_HEAD = (
    "> 本路书由 AI 基于 {when} 的联网检索结果生成，**仅供参考**。\n"
    "> 票价、开放时间、预约政策等信息可能随时变动，出行前请通过下方【官方渠道】核实。"
)


def is_safe_url(url: str | None) -> bool:
    """链接是否可安全写入导出文件（协议白名单 + 必须有主机名）。"""
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme.lower() in SAFE_URL_SCHEMES and bool(parsed.netloc)


def safe_url(url: str | None) -> str:
    """不安全时返回空串，模板据此退化为纯文本展示。"""
    if is_safe_url(url):
        return str(url)
    if url:
        logger.warning(
            "已拦截不安全的引用链接",
            extra={"extra_fields": {"scheme": str(url)[:32]}},
        )
    return ""


def format_when(searched_at: datetime | None) -> str:
    return searched_at.strftime("%Y-%m-%d %H:%M") if searched_at else "未知时间"


def disclaimer_text(searched_at: datetime | None) -> str:
    return DISCLAIMER_HEAD.format(when=format_when(searched_at))


def budget_coverage_markdown(detail: VersionDetail) -> str:
    overview = detail.budget_overview
    if not overview or not overview.coverage_warnings:
        return ""
    return (
        "\n\n## 费用覆盖提醒\n\n"
        + "\n".join(f"- {warning}" for warning in overview.coverage_warnings)
        + "\n\n费用清单仅含已列项目，暂不能据此认定整趟旅行节省。\n"
    )


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
        url = safe_url(citation.url)
        if not url:
            # 链接不安全：保留编号但不做成链接，避免导出文件带恶意跳转
            return f"[{index}]"
        return f"[[{index}]]({url})"

    linked = _CITATION_PATTERN.sub(replace, markdown)
    if missing:
        logger.warning(
            "路书引用了不存在的编号（疑似编造）",
            extra={"extra_fields": {"missing": sorted(set(missing))}},
        )
    return linked, missing


class TodoRenderItem(TypedDict):
    """送入模板的待办项。用 TypedDict 而非 dict[str, object] ——
    否则模板侧每次取值都要面对 ``object``，既丢类型也丢可读性。"""

    what: str
    when: str | None
    note: str | None
    channel: VerificationChannel


def build_todos(todos: list[TodoItem]) -> list[TodoRenderItem]:
    """把待办事项与官方渠道配对，供模板渲染。

    大模型只说了「要办什么」，「去哪办」由 ``channel_for`` 决定 ——
    模型从不接触办理链接（规划 §19.5）。
    """
    items: list[TodoRenderItem] = []
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
    """渲染 Markdown 参考资料列表（不安全的链接退化为纯文本）。"""
    lines: list[str] = []
    for citation in citations:
        when = citation.retrieved_at.strftime("%Y-%m-%d %H:%M")
        url = safe_url(citation.url)
        title = f"[{citation.title}]({url})" if url else citation.title
        lines.append(
            f"{citation.index}. {title}  \n   <sub>{citation.provider} · 抓取于 {when}</sub>"
        )
    return lines
