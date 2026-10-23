"""现有编排的只读投影，不作为另一份可执行工作流。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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
