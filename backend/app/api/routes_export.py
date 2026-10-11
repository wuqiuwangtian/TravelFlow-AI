"""导出接口（规划 §9.2）。

返回文件流并带上文件名，浏览器会直接触发下载。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.core.deps import get_store
from app.core.errors import InvalidRequest
from app.export import (
    render_html,
    render_markdown,
    suggest_html_filename,
    suggest_md_filename,
)
from app.storage.session_store import SessionStore

router = APIRouter(prefix="/api", tags=["export"])


@router.get("/versions/{version_id}/export", summary="导出版本")
async def export_version(
    version_id: str,
    format: str = "md",
    store: SessionStore = Depends(get_store),
) -> Response:
    """导出指定版本为 Markdown 或单文件 HTML。"""
    detail = await store.get_version_detail(version_id)
    if detail is None:
        raise InvalidRequest("版本不存在，无法导出")

    if format not in {"md", "html"}:
        raise InvalidRequest("导出格式只支持 md 或 html")

    title = await _session_title(store, detail.version.branch_id)

    if format == "html":
        content = render_html(detail, session_title=title, todos=detail.todos)
        filename = suggest_html_filename(title)
        media_type = "text/html"
    else:
        content = render_markdown(detail, session_title=title, todos=detail.todos)
        filename = suggest_md_filename(title)
        media_type = "text/markdown"

    return Response(
        content=content,
        media_type=f"{media_type}; charset=utf-8",
        headers={
            # RFC 5987：中文文件名需要编码，否则部分浏览器会乱码
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
        },
    )


async def _session_title(store: SessionStore, branch_id: str) -> str:
    branch = await store.get_branch(branch_id)
    if branch is None:
        return "旅行路书"
    session = await store.get_session(branch.session_id)
    return session.title if session else "旅行路书"
