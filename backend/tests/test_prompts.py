"""Prompt 模板测试 —— 守护「防幻觉」与「不硬编码年份」两条设计约束。"""

from __future__ import annotations

import pytest

from app.agents.prompt_loader import available_prompts, load_prompt, render_prompt


def test_all_expected_prompts_exist() -> None:
    assert set(available_prompts()) >= {"collect", "plan", "refine", "intent"}


def test_no_prompt_hardcodes_a_year() -> None:
    """回归阶段① P2-6：旧项目把 ``2024`` 写死在检索 query 与 Prompt 里。"""
    for name in available_prompts():
        text = load_prompt(name)
        for year in ("2023", "2024", "2025"):
            assert year not in text, f"{name}.md 中不应出现硬编码年份 {year}"


def test_plan_and_refine_forbid_url_output() -> None:
    """★ 防幻觉的核心约束：大模型不得输出任何 URL。"""
    for name in ("plan", "refine"):
        text = load_prompt(name)
        assert "不要输出任何 URL" in text, f"{name}.md 必须明确禁止输出 URL"


def test_plan_prompt_has_detail_level_slots() -> None:
    text = load_prompt("plan")
    for placeholder in ("$detail_name", "$target_length", "$detail_requirements"):
        assert placeholder in text


def test_render_fails_loudly_on_missing_variable() -> None:
    """缺变量必须立刻报错，而不是把 ``$var`` 原样送给模型。"""
    with pytest.raises(KeyError):
        render_prompt("collect")


def test_render_collect_with_variables() -> None:
    rendered = render_prompt("collect", collected_json='{"destination": "重庆"}')

    assert "重庆" in rendered
    assert "$collected_json" not in rendered


def test_render_plan_with_variables() -> None:
    rendered = render_prompt(
        "plan",
        destination="重庆",
        origin="北京",
        duration_days=3,
        budget=2000,
        travelers=2,
        preferences="美食",
        numbered_sources="[1] 某攻略 — 摘要",
        detail_name="标准",
        target_length="1300~1700",
        detail_requirements="包含交通与住宿建议",
    )

    assert "$" not in rendered, "渲染后不应残留任何占位符"
    assert "[1] 某攻略" in rendered
