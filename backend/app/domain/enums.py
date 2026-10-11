"""领域枚举 —— 状态与档位的强类型定义。

对照阶段①缺陷 P1-7：旧项目用裸字符串表示状态（``status: str``），
拼写错误不会有任何提示，且无法穷举取值。此处全部改为枚举。

使用 ``StrEnum``：成员本身就是 ``str``，可直接用于 JSON 序列化，
``str(PlanStatus.COLLECTING) == "collecting"``。
"""

from __future__ import annotations

from enum import StrEnum


class PlanStatus(StrEnum):
    """会话状态机（见规划 §8.2）。"""

    COLLECTING = "collecting"  # 需求收集中
    RESEARCHING = "researching"  # 联网检索中
    PLANNING = "planning"  # 生成路书中
    REVIEWING = "reviewing"  # 待用户确认 / 修改
    FINISHED = "finished"  # 已定稿


class DetailLevel(StrEnum):
    """路书详细度档位（见规划 §4）。"""

    L1_MINIMAL = "L1_minimal"  # 极简
    L2_STANDARD = "L2_standard"  # 标准（默认）
    L3_DETAILED = "L3_detailed"  # 详细
    L4_BUTLER = "L4_butler"  # 保姆级


class ExportFormat(StrEnum):
    """导出格式。"""

    MARKDOWN = "md"
    HTML = "html"


class TodoKind(StrEnum):
    """待办理事项类型 —— 用于匹配官方验证渠道（见规划 §19.4）。"""

    RAILWAY = "railway"  # 火车票
    FLIGHT = "flight"  # 机票
    TICKET = "ticket"  # 景点门票
    RESERVATION = "reservation"  # 场馆预约
    HOTEL = "hotel"  # 住宿
    OTHER = "other"
