"""现有编排投影及受限的单日路线优化契约。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.itinerary import BudgetOverview, DayPlan
from app.domain.models import BranchInfo, SessionInfo, VersionInfo

NodeId = Literal["collector", "researcher", "planner", "refiner"]
NodeState = Literal["waiting", "needs_input", "available", "ready", "interrupted", "unknown"]


class WorkflowFact(BaseModel):
    label: str
    value: str


class WorkflowNode(BaseModel):
    id: NodeId
    title: str
    description: str
    state: NodeState
    inputs: list[str]
    outputs: list[WorkflowFact]
    notes: list[str] = Field(default_factory=list)


class WorkflowEdge(BaseModel):
    source: NodeId
    target: NodeId
    trigger: Literal["automatic", "next_message"]
    label: str


class WorkflowView(BaseModel):
    session: SessionInfo
    branch: BranchInfo
    version: VersionInfo | None
    historical: bool
    source: Literal["checkpoint", "saved_version", "empty"]
    checkpoint_status: Literal["available", "missing", "unavailable", "not_configured"]
    checkpoint_at: datetime | None
    observed_at: datetime
    busy: bool
    plan_status: str | None
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    focus_node: NodeId
    warnings: list[str] = Field(default_factory=list)


class PlannerRerunInput(BaseModel):
    request_id: str = Field(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    day: int = Field(ge=1, le=60)
    travel_mode: Literal["auto", "walking", "transit", "driving"] = "auto"
    instruction: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class DayOrder(BaseModel):
    activity_ids: list[str] = Field(min_length=1, max_length=80)


class PlannerPreview(BaseModel):
    request_id: str
    branch_id: str
    base_version_id: str
    source_checkpoint: str
    parameters: PlannerRerunInput
    before: DayPlan
    after: DayPlan
    protected_ids: list[str]
    budget_before: BudgetOverview
    budget_after: BudgetOverview
    created_at: datetime
    conflicts: list[str] = Field(default_factory=list)
