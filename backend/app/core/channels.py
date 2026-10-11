"""官方验证渠道注册表（规划 §19.4）。

用户反馈：「关于这些信息**哪怕 AI 说的再信誓旦旦我也必须得自己去官网验证一下**」。
因此除了「信息来源」（引用），系统还要给出「办理 / 核验渠道」。

**为什么可以内置**：官方渠道的稳定性远高于价格 ——
12306 十几年的入口没有变过，而票价一年可能调几次。
因此这里只登记渠道本身，**绝不登记任何价格、额度、优惠政策**。

**维护成本控制**：
- 每条渠道带 ``verified_at``，便于将来提示「该渠道信息核实于 X 年 X 月」；
- 支持通过配置覆盖，不必改代码。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# 全部渠道信息的最后核实日期（集中一处，便于整体更新）
CHANNELS_VERIFIED_AT = date(2026, 10, 11)


@dataclass(frozen=True, slots=True)
class VerificationChannel:
    """一个官方办理 / 核验渠道。"""

    kind: str
    label: str
    url: str | None = None
    app: str | None = None
    miniprogram: str | None = None
    note: str = ""
    verified_at: date | None = CHANNELS_VERIFIED_AT

    @property
    def has_direct_link(self) -> bool:
        """是否有可直接跳转的官方网址。"""
        return bool(self.url)


OFFICIAL_CHANNELS: dict[str, VerificationChannel] = {
    "railway": VerificationChannel(
        kind="railway",
        label="中国铁路 12306",
        url="https://www.12306.cn",
        app="铁路12306",
        miniprogram="铁路12306",
        note="铁路售票唯一官方渠道，请勿通过第三方代购",
    ),
    "flight": VerificationChannel(
        kind="flight",
        label="航空公司官方渠道",
        note="请通过所乘航司官网或官方 App 查询购票；也可在微信搜索航司名称的官方小程序",
    ),
    "ticket": VerificationChannel(
        kind="ticket",
        label="景点官方购票渠道",
        note="可在微信搜索「景点名称 + 官方」公众号或小程序",
    ),
    "reservation": VerificationChannel(
        kind="reservation",
        label="场馆官方预约渠道",
        note="各地预约政策调整频繁，请以官方公告为准；可在微信搜索场馆名称的公众号",
    ),
    "hotel": VerificationChannel(
        kind="hotel",
        label="酒店官方或主流预订平台",
        note="建议直接联系酒店确认房态与价格",
    ),
    "other": VerificationChannel(
        kind="other",
        label="请通过官方渠道核实",
        note="如无法确认官方渠道，可在微信搜索服务名称 + 官方",
    ),
}


def channel_for(kind: str) -> VerificationChannel:
    """按类型取渠道；未知类型回落到 ``other``，**绝不返回空**。"""
    return OFFICIAL_CHANNELS.get(kind) or OFFICIAL_CHANNELS["other"]
