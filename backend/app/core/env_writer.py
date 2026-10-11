"""把设置写回 ``.env`` 文件（规划 §9.2 的 ``PUT /api/settings``）。

为什么需要它：面向小白的界面若只能「查看配置」而不能「修改配置」，
用户仍然要手写 ``.env`` —— 那就谈不上好用。

安全边界：
- **只接受白名单字段**（``SettingsUpdate`` 定义），不接受任意键；
- 密钥写入后**不回显**；
- 写入采用「读-改-写整文件」，保留用户自己的注释与顺序。
"""

from __future__ import annotations

from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

# 字段名 → .env 中的键名
FIELD_TO_ENV: dict[str, str] = {
    "llm_provider": "LLM_PROVIDER",
    "llm_model": "LLM_MODEL",
    "llm_api_key": "LLM_API_KEY",
    "llm_base_url": "LLM_BASE_URL",
    "search_provider": "SEARCH_PROVIDER",
    "search_api_key": "SEARCH_API_KEY",
    "searxng_base_url": "SEARXNG_BASE_URL",
    "default_detail_level": "DEFAULT_DETAIL_LEVEL",
}


def write_env(path: str | Path, updates: dict[str, str]) -> list[str]:
    """把 ``updates`` 合并进 .env 文件，返回实际发生变化的键。

    保留原有注释与行序；文件中不存在的键会被追加到末尾。
    """
    env_path = Path(path)
    lines: list[str] = []
    if env_path.is_file():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)
    changed: list[str] = []

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key not in remaining:
            continue

        new_value = remaining.pop(key)
        old_value = stripped.split("=", 1)[1].strip()
        if old_value == new_value:
            continue
        lines[index] = f"{key}={new_value}"
        changed.append(key)

    for key, value in remaining.items():
        lines.append(f"{key}={value}")
        changed.append(key)

    if not lines or lines[-1] != "":
        lines.append("")

    env_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("配置已写入 .env", extra={"extra_fields": {"changed": ",".join(changed)}})
    return changed
