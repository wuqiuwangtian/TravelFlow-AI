"""Prompt 模板加载（规划 §7「版本化的 Prompt 管理」）。

Prompt 放 ``prompts/*.md``，改文案不必动代码，也便于阶段③做快照测试。

占位符采用 ``string.Template`` 的 ``$var`` 语法 ——
刻意避开 ``str.format`` 的花括号，因为 Prompt 里包含 JSON 示例。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from string import Template

PROMPT_DIR = Path(__file__).parent / "prompts"


class PromptNotFoundError(FileNotFoundError):
    """Prompt 模板缺失 —— 属于部署问题，启动即应发现。"""


@lru_cache(maxsize=64)
def load_prompt(name: str) -> str:
    """读取并缓存一个 Prompt 模板。"""
    path = PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise PromptNotFoundError(f"Prompt 模板不存在：{path}")
    return path.read_text(encoding="utf-8").strip()


def render_prompt(name: str, **variables: object) -> str:
    """渲染模板。

    使用**严格**替换：缺少变量会立刻抛 ``KeyError``，
    避免把 ``$var`` 原样送给大模型（那会让模型看到语法噪音）。
    """
    template = Template(load_prompt(name))
    try:
        return template.substitute(**variables)
    except KeyError as exc:
        raise KeyError(f"Prompt「{name}」缺少变量：{exc}") from exc


def available_prompts() -> list[str]:
    """列出全部模板名（供测试与自检使用）。"""
    return sorted(p.stem for p in PROMPT_DIR.glob("*.md"))
