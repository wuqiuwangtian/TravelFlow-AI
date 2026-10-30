"""对话修改意图与旅中时间窗口，供节点和待澄清状态共享。"""

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.itinerary import ClockTime
from app.domain.models import Requirements


class OnTripUpdate(BaseModel):
    day: int | None = Field(default=None, ge=1, le=60)
    location_name: str = Field(default="", max_length=100)
    city: str = Field(default="", max_length=50)
    time: ClockTime
    start_day_offset: int = Field(
        default=0, ge=0, le=60, description="出发时刻相对该行程日的天数偏移，当日0、次日1"
    )
    end_time: ClockTime | None = None
    end_day_offset: int = Field(
        default=0,
        ge=0,
        le=60,
        description="结束时刻相对该行程日的天数偏移，当日0、次日1；不擅自推断跨日",
    )
    end_location_name: str = Field(default="", max_length=100)


class RefineIntent(BaseModel):
    """意图判定结果 —— 用枚举替代字符串包含判断，消除误判。"""

    intent: Literal["approve", "modify", "unclear"]
    summary: str = Field(default="", description="一句话概括用户想要什么")
    clear_preferences: bool = Field(
        default=False, description="用户明确要求清空或不沿用本次偏好时为true"
    )
    requirements: Requirements = Field(
        default_factory=Requirements, description="只提取本次明确改变的出行需求"
    )
    needs_research: bool = Field(
        default=False,
        description="新增景点、酒店、修改目的地或日期等需要新资料时为 true，改措辞、顺序则为 false",
    )
    on_trip: OnTripUpdate | None = Field(
        default=None,
        description="用户明确描述旅途中起点和出发时间时提取，未提及则为空；不猜测时间或地点。",
    )
