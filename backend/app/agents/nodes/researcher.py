"""节点二：联网调研（researcher）。

对照阶段①的两处关键改进：

* **P2-6** query 硬编码 ``2024`` → 年份改由 ``datetime.now().year`` 动态注入，
  从根本上消除「产出当天即过时」；
* **P1-6** 旧项目注释写"并行调用搜索工具"实则串行 → 这里真正使用
  ``asyncio.gather``，把耗时从「三路之和」压到「最慢一路」。

本节点**不使用大模型**：query 由程序构造，检索结果由程序持有（防幻觉根基）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from app.agents.state import PlanState
from app.agents.streaming import emit_notice, emit_progress
from app.core.errors import SearchUnavailable
from app.core.logging import get_logger
from app.domain.enums import PlanStatus
from app.domain.models import Requirements, ResearchBundle
from app.search.base import SearchProvider, SearchResult

logger = get_logger(__name__)

# 交通检索优先看官方与主流票务平台；不支持的 Provider 会自动忽略该参数
TRANSPORT_DOMAINS = ("12306.cn", "ctrip.com", "qunar.com")


def build_queries(requirements: Requirements, year: int) -> dict[str, str]:
    """构造三类定向检索 query。

    ``year`` 由调用方传入当前年份 —— 测试可直接断言 query 里不含硬编码年份。
    """
    destination = requirements.destination or ""
    origin = requirements.origin or ""
    return {
        "transport": f"{origin} 到 {destination} 火车票 高铁 机票 价格 时刻表 {year}",
        "attractions": f"{destination} 必去景点 门票价格 开放时间 {year}",
        "reviews": f"{destination} 旅游 避雷 坑 真实体验 {year}",
    }


def make_researcher_node(search: SearchProvider, *, max_results: int = 5, focus: str = ""):
    """构造 researcher 节点。"""

    async def researcher(state: PlanState) -> dict:
        emit_progress("research")
        requirements = state["requirements"]
        queries = build_queries(requirements, datetime.now().year)
        if requirements.departure_date:
            queries = {
                key: f"{query} {requirements.departure_date.isoformat()}"
                for key, query in queries.items()
            }
        if focus:
            queries["attractions"] = f"{requirements.destination} {focus[:300]} 开放时间 价格 官方"

        # ★ 三路并行；单路失败不拖垮整体
        outcomes = await asyncio.gather(
            search.search(queries["transport"], max_results=4, domains=TRANSPORT_DOMAINS),
            search.search(queries["attractions"], max_results=max_results),
            search.search(queries["reviews"], max_results=max_results),
            return_exceptions=True,
        )

        bucket_names = ("transport", "attractions", "reviews")
        buckets: dict[str, list[SearchResult]] = {}
        failures = 0
        for name, outcome in zip(bucket_names, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                failures += 1
                buckets[name] = []
                logger.warning("检索「%s」失败: %s", name, outcome)
            else:
                buckets[name] = list(outcome)

        if failures == len(bucket_names):
            # 保留 Provider 已整理的安全提示（鉴权、余额、限速等），不误报为网络故障。
            messages = list(
                dict.fromkeys(
                    outcome.message
                    for outcome in outcomes
                    if isinstance(outcome, SearchUnavailable)
                )
            )
            raise SearchUnavailable(
                "；".join(messages)
                if messages
                else f"检索服务（{search.name}）暂时不可用，请稍后重试或在「设置」中检查检索服务。",
                detail=f"provider={search.name} 三路检索全部失败",
            )

        missing = sum(not results for results in buckets.values())
        if missing == len(bucket_names):
            raise SearchUnavailable(
                "暂未检索到可用的旅行资料，请调整目的地或稍后重试；"
                "也可在「设置」中检查或更换检索服务。",
                detail=f"provider={search.name} empty={missing - failures} failed={failures}",
            )
        if missing:
            # 零命中和服务失败都可能造成资料缺口，不能把空结果当成完整调研。
            emit_notice(
                f"有 {missing} 路检索未取得可用资料，路书会基于其余结果生成，可能不够全面。"
            )

        bundle = ResearchBundle(
            transport=buckets["transport"],
            attractions=buckets["attractions"],
            reviews=buckets["reviews"],
            queries=list(queries.values()),
            searched_at=datetime.now(),
        )
        logger.info(
            "检索完成",
            extra={
                "extra_fields": {
                    "transport": len(bundle.transport),
                    "attractions": len(bundle.attractions),
                    "reviews": len(bundle.reviews),
                    "failed": failures,
                }
            },
        )

        return {
            "research": bundle,
            "status": PlanStatus.PLANNING,
            "updated_at": datetime.now(),
        }

    return researcher
