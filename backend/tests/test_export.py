"""导出层测试。

最重要的一条：**导出文件里的链接必须与 Citation 中的 URL 完全一致** ——
即链接来自程序透传，而非大模型生成（规划 §19.3）。
"""

from __future__ import annotations

import re
from datetime import datetime

from app.domain.enums import DetailLevel, TodoKind
from app.domain.models import Citation, TodoItem, VersionDetail, VersionInfo
from app.export.html_exporter import render_html
from app.export.markdown_exporter import render_markdown

SEARCHED_AT = datetime(2026, 10, 11, 15, 32)
BENDIBAO = "https://m.cq.bendibao.com/tour/49116.shtm"
RAILWAY = "https://www.12306.cn/"

CITATIONS = [
    Citation(
        index=1,
        title="重庆市内必去景点攻略",
        url=BENDIBAO,
        provider="tavily",
        retrieved_at=SEARCHED_AT,
    ),
    Citation(
        index=2, title="中国铁路 12306", url=RAILWAY, provider="tavily", retrieved_at=SEARCHED_AT
    ),
]

BODY = "## 交通建议\n\n高铁二等座约 500 元[1]，购票请见[2]。\n\n> **避雷**：景区「金西梅」慎买[1]。"
TODOS = [
    TodoItem(kind=TodoKind.RAILWAY, what="北京 → 重庆 高铁二等座", when="建议提前 15 天购票"),
    TodoItem(kind=TodoKind.TICKET, what="长江索道门票", note="可从南站上车，排队更少"),
]


def _detail(markdown: str = BODY) -> VersionDetail:
    version = VersionInfo(
        id="v1",
        branch_id="b1",
        seq=1,
        summary="标准版路书",
        detail_level=DetailLevel.L2_STANDARD.value,
        created_at=SEARCHED_AT,
    )
    return VersionDetail(
        version=version, markdown=markdown, citations=CITATIONS, searched_at=SEARCHED_AT
    )


# ============================================================ Markdown
def test_links_come_from_citations_not_the_model() -> None:
    """★ 链接必须是程序从 Citation 填回的原始 URL。"""
    output = render_markdown(_detail(), session_title="重庆之行")

    assert f"[[1]]({BENDIBAO})" in output
    assert f"[[2]]({RAILWAY})" in output


def test_disclaimer_appears_at_head_and_tail() -> None:
    output = render_markdown(_detail(), session_title="重庆之行")

    body_pos = output.index("## 交通建议")
    first = output.index("仅供参考")
    last = output.rindex("仅供参考")

    assert first < body_pos < last, "免责声明应同时出现在路书首尾"


def test_metadata_shows_search_time() -> None:
    output = render_markdown(_detail())

    assert "2026-10-11 15:32" in output


def test_official_channels_are_attached_to_todos() -> None:
    """待办事项必须带上官方渠道 —— 用户要能自己去验证。"""
    output = render_markdown(_detail(), todos=TODOS)

    assert "需要提前办理的事项" in output
    assert "中国铁路 12306" in output
    assert RAILWAY in output
    assert "微信搜索「铁路12306」" in output  # 无直链时的可操作路径
    assert "微信搜索" in output  # 门票类给出小程序路径


def test_references_section_lists_sources() -> None:
    output = render_markdown(_detail())

    assert "## 参考资料" in output
    assert "重庆市内必去景点攻略" in output


def test_unknown_citation_index_is_left_intact() -> None:
    """模型编造的引用编号不得变成链接，应原样保留以便暴露问题。"""
    output = render_markdown(_detail(markdown="这里有内容[9]。"))

    assert "[9]" in output
    assert "[9](" not in output


# ============================================================ HTML
def test_html_is_single_file_without_external_assets() -> None:
    output = render_html(_detail(), todos=TODOS)

    assert output.startswith("<!DOCTYPE html>")
    # 不加载任何外部 CSS / JS / 字体
    assert not re.search(r'<link[^>]+href="https?://', output)
    assert not re.search(r'<script[^>]+src="https?://', output)
    assert "@import" not in output
    assert "cdn." not in output


def test_html_contains_disclaimer_and_channels() -> None:
    output = render_html(_detail(), todos=TODOS, session_title="重庆之行")

    assert "仅供参考" in output
    assert "中国铁路 12306" in output
    assert "需要提前办理的事项" in output


def test_html_has_print_and_motion_styles() -> None:
    output = render_html(_detail())

    assert "@media print" in output
    assert "prefers-reduced-motion" in output


def test_html_escapes_model_output() -> None:
    """大模型输出会被当成不可信内容转义，避免注入。"""
    output = render_html(_detail(markdown='<script>alert("xss")</script>'))

    assert "<script>alert" not in output


def test_markdown_filename_is_sanitised() -> None:
    from app.export.markdown_exporter import suggest_filename

    assert "/" not in suggest_filename("重庆/成都 之行")
    assert suggest_filename("重庆之行") == "重庆之行.md"
