"""Markdown 导出（规划 §11）。

产出结构：
标题 → 元信息 → **免责声明** → 正文（引用已链接化）→ 办理与验证 → 参考资料 → **免责声明**

免责声明刻意由程序拼接而非交给大模型 —— 保证它不会被遗漏或改写。
"""

from __future__ import annotations

from app.domain.models import TodoItem, VersionDetail
from app.export.common import (
    budget_coverage_markdown,
    build_todos,
    disclaimer_text,
    format_when,
    linkify_citations,
    reference_lines,
)


def render_markdown(
    detail: VersionDetail,
    *,
    session_title: str = "旅行路书",
    todos: list[TodoItem] | None = None,
) -> str:
    """渲染 Markdown 文本。"""
    linked, _missing = linkify_citations(detail.markdown, detail.citations)

    parts: list[str] = [
        f"# {session_title}",
        "",
        f"<sub>信息检索时间：{format_when(detail.searched_at)}"
        f" · 引用 {len(detail.citations)} 个来源</sub>",
        "",
        disclaimer_text(detail.searched_at),
        "",
        "---",
        "",
        linked.strip() + budget_coverage_markdown(detail),
        "",
    ]

    todo_items = build_todos(todos or [])
    if todo_items:
        parts += ["---", "", "## 需要提前办理的事项", ""]
        for item in todo_items:
            channel = item["channel"]
            parts.append(f"### {item['what']}")
            if item["when"]:
                parts.append(f"- **建议时间**：{item['when']}")
            parts.append(f"- **官方渠道**：{channel.label}")
            if channel.url:
                parts.append(f"  - 网址：{channel.url}")
            if channel.app:
                parts.append(f"  - App：{channel.app}")
            if channel.miniprogram:
                parts.append(f"  - 小程序：微信搜索「{channel.miniprogram}」")
            if item["note"]:
                parts.append(f"  - 提示：{item['note']}")
            if channel.note:
                parts.append(f"  - {channel.note}")
            parts.append("")

    if detail.citations:
        parts += ["---", "", "## 参考资料", "", *reference_lines(detail.citations), ""]

    parts += ["---", "", disclaimer_text(detail.searched_at), ""]
    return "\n".join(parts)


def suggest_filename(session_title: str) -> str:
    """生成一个对用户友好的文件名（去掉不适合做文件名的字符）。"""
    safe = "".join(ch for ch in session_title if ch not in '\\/:*?"<>|').strip() or "旅行路书"
    return f"{safe}.md"
