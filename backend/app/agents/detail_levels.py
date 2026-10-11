"""路书详细度档位定义（规划 §4）。

篇幅 = 基础字数 + 每日字数 × 天数，
保证「一日游」与「一周行程」的篇幅有**明显差距**（用户明确要求）。
"""

from __future__ import annotations

from app.domain.enums import DetailLevel

# (基础字数, 每日字数)
_LENGTH_FORMULA: dict[DetailLevel, tuple[int, int]] = {
    DetailLevel.L1_MINIMAL: (200, 150),
    DetailLevel.L2_STANDARD: (400, 350),
    DetailLevel.L3_DETAILED: (600, 600),
    DetailLevel.L4_BUTLER: (900, 1000),
}

_NAMES: dict[DetailLevel, str] = {
    DetailLevel.L1_MINIMAL: "极简",
    DetailLevel.L2_STANDARD: "标准",
    DetailLevel.L3_DETAILED: "详细",
    DetailLevel.L4_BUTLER: "保姆级",
}

_REQUIREMENTS: dict[DetailLevel, str] = {
    DetailLevel.L1_MINIMAL: (
        "只写每日行程要点与关键避雷提示。不写住宿推荐、不写费用预估表、不展开背景介绍。"
    ),
    DetailLevel.L2_STANDARD: (
        "包含交通方式建议、住宿区域建议、费用预估表（经济型 / 舒适型双档）。"
        "每日行程给出主要景点与顺序即可。"
    ),
    DetailLevel.L3_DETAILED: (
        "在标准档基础上，增加具体交通接驳方案、每餐推荐、"
        "每到一站的备选方案，以及更细的时间安排建议。"
    ),
    DetailLevel.L4_BUTLER: (
        "在详细档基础上，增加逐时段的时间点安排、购票与预约指引、"
        "行李清单、应急联系方式、当地习俗与礼仪提醒。"
        "目标是用户不需要再做任何功课。"
    ),
}


def detail_name(level: DetailLevel) -> str:
    return _NAMES.get(level, _NAMES[DetailLevel.L2_STANDARD])


def detail_requirements(level: DetailLevel) -> str:
    return _REQUIREMENTS.get(level, _REQUIREMENTS[DetailLevel.L2_STANDARD])


def estimate_length(level: DetailLevel, duration_days: int | None) -> int:
    """按档位与天数估算目标字数。"""
    base, per_day = _LENGTH_FORMULA.get(level, _LENGTH_FORMULA[DetailLevel.L2_STANDARD])
    days = max(1, duration_days or 1)
    return base + per_day * days


def target_length_text(level: DetailLevel, duration_days: int | None) -> str:
    """给大模型的篇幅区间文本，留 ±15% 浮动空间。"""
    target = estimate_length(level, duration_days)
    return f"{int(target * 0.85)}~{int(target * 1.15)}"


# 各档位包含哪些章节（前端展示与测试断言用）
SECTIONS_BY_LEVEL: dict[DetailLevel, tuple[str, ...]] = {
    DetailLevel.L1_MINIMAL: ("行程概览", "每日行程", "避雷贴士", "参考资料"),
    DetailLevel.L2_STANDARD: (
        "行程概览",
        "交通建议",
        "住宿推荐",
        "每日行程",
        "费用预估",
        "避雷贴士",
        "参考资料",
    ),
    DetailLevel.L3_DETAILED: (
        "行程概览",
        "交通建议",
        "住宿推荐",
        "每日行程",
        "费用预估",
        "避雷贴士",
        "参考资料",
    ),
    DetailLevel.L4_BUTLER: (
        "行程概览",
        "交通建议",
        "住宿推荐",
        "每日行程",
        "费用预估",
        "避雷贴士",
        "参考资料",
    ),
}
