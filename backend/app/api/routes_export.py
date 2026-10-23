"""导出接口（规划 §9.2）。

返回文件流并带上文件名，浏览器会直接触发下载。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.core.deps import get_store
from app.core.errors import InvalidRequest
from app.domain.enums import ExportFormat
from app.export import (
    render_html,
    render_markdown,
    suggest_html_filename,
    suggest_md_filename,
)
from app.storage.session_store import SessionStore

router = APIRouter(prefix="/api", tags=["export"])


@router.get("/versions/{version_id}/recap/export")
async def export_recap(
    version_id: str,
    format: ExportFormat = ExportFormat.MARKDOWN,
    include_photos: bool = False,
    store: SessionStore = Depends(get_store),
) -> Response:
    from app.domain.models import Requirements
    from app.services.recap_service import recap_view, render_recap

    detail = await store.get_version_detail(version_id)
    if detail is None or detail.itinerary is None:
        raise InvalidRequest("未找到可回顾的每日行程")
    as_html = format == ExportFormat.HTML
    content = render_recap(
        recap_view(detail.itinerary, detail.requirements or Requirements()), as_html=as_html
    )
    if include_photos:
        if not as_html:
            raise InvalidRequest("图文回顾请使用HTML格式，照片会嵌入文件以便离线查看。")
        from app.services.photo_service import photo_export_html

        branch = await store.get_branch(detail.version.branch_id)
        if branch is None:
            raise InvalidRequest("旅行不存在")
        content = await photo_export_html(content, detail.itinerary, store, branch.session_id)
    return Response(
        content,
        media_type="text/html" if as_html else "text/markdown",
        headers={
            "Content-Disposition": "attachment; filename=travel-recap."
            + ("html" if as_html else "md"),
            "Content-Security-Policy": (
                "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/versions/{version_id}/export", summary="导出版本")
async def export_version(
    version_id: str,
    format: ExportFormat = ExportFormat.MARKDOWN,
    store: SessionStore = Depends(get_store),
) -> Response:
    """导出指定版本为 Markdown 或单文件 HTML。

    用枚举而不是裸字符串：非法格式由 FastAPI 直接拦成 422，
    不需要在函数体里手写校验，也让 OpenAPI 文档列出可选值。
    """
    detail = await store.get_version_detail(version_id)
    if detail is None:
        raise InvalidRequest("版本不存在，无法导出")

    title = await _session_title(store, detail.version.branch_id)

    # 用 == 而非 is：FastAPI 在不同版本下可能传入枚举实例或等值字符串
    if format == ExportFormat.HTML:
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
