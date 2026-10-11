"""HTML 导出（规划 §11）。

特性：
- **单文件、零外部依赖**（CSS/JS 全部内联），断网也能打开；
- 图文并茂 + 适度动画（滚动淡入、时间轴点亮、数字滚动）；
- ``@media print`` 关闭动画并适配 A4，用户可直接 Ctrl+P 存为 PDF；
- ``prefers-reduced-motion`` 下自动降级。
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt

from app.domain.models import TodoItem, VersionDetail
from app.export.common import (
    build_todos,
    disclaimer_text,
    format_when,
    linkify_citations,
)

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

# ★ ``html=False`` 让原始 HTML 标签被转义。
# 大模型输出与检索内容都属于**不可信输入**，绝不能作为原始 HTML 注入导出文件。
_md = (
    MarkdownIt("commonmark", {"html": False, "linkify": False})
    .enable("table")
    .enable("strikethrough")
)


def render_html(
    detail: VersionDetail,
    *,
    session_title: str = "旅行路书",
    todos: list[TodoItem] | None = None,
) -> str:
    """渲染单文件 HTML。"""
    linked, _missing = linkify_citations(detail.markdown, detail.citations)
    body_html = _md.render(linked)

    template = _env.get_template("report.html.jinja")
    return template.render(
        title=session_title,
        body_html=body_html,
        citations=detail.citations,
        todos=build_todos(todos or []),
        disclaimer=disclaimer_text(detail.searched_at),
        searched_at_text=format_when(detail.searched_at),
    )


def suggest_filename(session_title: str) -> str:
    safe = "".join(ch for ch in session_title if ch not in '\\/:*?"<>|').strip() or "旅行路书"
    return f"{safe}.html"
