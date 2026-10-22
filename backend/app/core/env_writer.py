"""把设置写回 ``.env`` 文件（规划 §9.2 的 ``PUT /api/system/settings``）。

为什么需要它：面向小白的界面若只能「查看配置」而不能「修改配置」，
用户仍然要手写 ``.env`` —— 那就谈不上好用。

安全边界：
- **只接受白名单字段**（``FIELD_TO_ENV``），不接受任意键；
- **值中禁止换行与控制字符** —— 否则可以靠 ``"x\\nAPP_HOST=0.0.0.0"``
  注入额外配置行，让白名单形同虚设（详见 ``_reject_unsafe``）；
- 写入后文件权限收紧为 ``0600``（默认 umask 下是 0644，同机其它用户可读）；
- 密钥写入后**不回显**；
- 写入采用「读-改-写整文件」，保留用户自己的注释与顺序。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from app.core.errors import InvalidRequest
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
    "amap_api_key": "AMAP_API_KEY",
    "searxng_base_url": "SEARXNG_BASE_URL",
    "default_detail_level": "DEFAULT_DETAIL_LEVEL",
}

#: .env 的文件权限：属主可读写，其他人一律不可见（里面是 API Key）
ENV_FILE_MODE = 0o600

MAP_SECTION = "# ---------------- 高德地图（选填）----------------"
MAP_HELP = (
    "# 使用高德 Web 服务类型的 Key，用于地点搜索、路线规划和每日地图。",
    "# 此 Key 与大模型的 LLM_API_KEY 独立；留空时不校验地图路线。",
    "# 查询超时单位为秒。",
)
MAP_FIELDS = ("AMAP_API_KEY", "AMAP_TIMEOUT_SECONDS")


def _with_map_section(lines: list[str]) -> list[str]:
    """把地图配置归入独立区块，兼容早期直接追加在数据库配置后的文件。"""
    entries: dict[str, str] = {}
    remaining: list[str] = []
    for line in lines:
        key = line.strip().split("=", 1)[0].strip()
        if "=" in line and key in MAP_FIELDS:
            entries[key] = line  # 与 dotenv 一致：重复项以最后一次为准
        else:
            remaining.append(line)
    if not entries:
        return lines
    entries.setdefault("AMAP_TIMEOUT_SECONDS", "AMAP_TIMEOUT_SECONDS=12")
    header = next(
        (
            i
            for i, line in enumerate(remaining)
            if re.match(r"^#\s*-+.*(?:高德|地图).*?-+\s*$", line)
        ),
        None,
    )
    if header is None:
        insertion = next(
            (
                i
                for i, line in enumerate(remaining)
                if re.match(r"^#\s*-+.*(?:生成偏好|数据).*?-+\s*$", line)
            ),
            len(remaining),
        )
        block = [
            MAP_SECTION,
            *MAP_HELP,
            *(entries[key] for key in MAP_FIELDS if key in entries),
            "",
        ]
        if insertion and remaining[insertion - 1].strip():
            block.insert(0, "")
        return remaining[:insertion] + block + remaining[insertion:]
    insertion = header + 1
    while (
        insertion < len(remaining)
        and remaining[insertion].startswith("#")
        and not re.match(r"^#\s*-+", remaining[insertion])
    ):
        insertion += 1
    if not any("LLM_API_KEY" in line for line in remaining[header:insertion]):
        remaining[insertion:insertion] = MAP_HELP
        insertion += len(MAP_HELP)
    remaining[insertion:insertion] = [entries[key] for key in MAP_FIELDS if key in entries]
    return remaining


def _reject_unsafe(key: str, value: str) -> str:
    """拒绝会破坏 .env 结构的值。

    ``.env`` 是**按行**解析的：值里出现换行，就等于凭空多写了一行配置。
    例如 ``llm_model = "a\\nAPP_HOST=0.0.0.0"`` 会让服务对局域网开放 ——
    所以这里必须在写入前拦住，而不是依赖上层的字段校验。
    """
    if "\n" in value or "\r" in value:
        raise InvalidRequest(f"{key} 不能包含换行符")
    # 其它控制字符（含制表符）同样没有合法用途
    if any(ord(ch) < 0x20 for ch in value):
        raise InvalidRequest(f"{key} 不能包含控制字符")
    if "\x00" in value:
        raise InvalidRequest(f"{key} 不能包含空字符")
    return value


def write_env(path: str | Path, updates: dict[str, str]) -> list[str]:
    """把 ``updates`` 合并进 .env 文件，返回实际发生变化的键。

    保留原有注释与行序；地图项集中放入独立区块，其余新键追加到末尾。
    """
    env_path = Path(path)
    lines: list[str] = []
    if env_path.is_file():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    remaining = {
        key: _reject_unsafe(key, "" if value is None else str(value))
        for key, value in updates.items()
    }
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

    lines = _with_map_section(lines)

    if not lines or lines[-1] != "":
        lines.append("")

    env_path.write_text("\n".join(lines), encoding="utf-8")
    _harden_permissions(env_path)
    logger.info("配置已写入 .env", extra={"extra_fields": {"changed": ",".join(changed)}})
    return changed


def _harden_permissions(env_path: Path) -> None:
    """把 ``.env`` 权限收紧到 0600（失败不影响功能，仅记录）。"""
    try:
        os.chmod(env_path, ENV_FILE_MODE)
    except OSError as exc:  # pragma: no cover - 平台相关（如 Windows）
        logger.debug("未能收紧 .env 权限: %s", exc)
