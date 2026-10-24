"""工程成稿采用：服务端生成预览，用户只提交来源与确认令牌。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.itinerary import BudgetOverview, DayDraft, Itinerary
from app.domain.models import Requirements, ResearchBundle, TodoItem


class StudioPlanDraft(BaseModel):
    adoptable: bool = Field(description="正文是否有唯一明确的每日行程，而非城市推荐或多个未决备选")
    reason: str = Field(default="", max_length=1000)
    requirements: Requirements
    days: list[DayDraft] = Field(default_factory=list, max_length=60)
    todos: list[TodoItem] = Field(default_factory=list)


class AdoptionInput(BaseModel):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    base_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_key: str = Field(pattern=r"^[a-f0-9]{64}$")


class AdoptionChange(BaseModel):
    day: int
    activity_id: str
    title: str
    status: Literal["added", "removed", "updated", "preserved", "unchanged"]
    reason: str = ""
    before: str = ""
    after: str = ""


class AdoptionPreview(BaseModel):
    request_id: str
    branch_id: str
    run_id: str
    node_id: str
    source_title: str
    source_revision: int
    source_created_at: datetime
    source_key: str
    base_key: str
    base_version_id: str | None
    before_requirements: Requirements
    requirements: Requirements
    itinerary: Itinerary
    markdown: str
    todos: list[TodoItem]
    research: ResearchBundle
    budget_before: BudgetOverview | None
    budget_after: BudgetOverview
    changes: list[AdoptionChange]
    retained: list[str]
    conflicts: list[str]
    warnings: list[str]
    created_at: datetime


class AdoptionContext(BaseModel):
    base_version_id: str | None
    base_key: str
    source_key: str
    blocker: str | None = None
    preview: AdoptionPreview | None = None
