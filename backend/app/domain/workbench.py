"""用户可编辑的生成工程：有类型的端口、DAG 与可重现的运行快照。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.domain.studio_candidates import CandidatePool
from app.domain.studio_data import BudgetRules, StudioBudgetData, StudioPlanData

NodeKind = Literal[
    "input",
    "search",
    "generate",
    "output",
    "plan",
    "route",
    "budget",
    "select",
    "candidates",
    "allocate",
]
PortType = Literal["text", "sources", "itinerary", "budget", "candidates"]


class NodeParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(default="", max_length=12000)
    prompt: str = Field(default="", max_length=6000)
    temperature: float = Field(default=0.5, ge=0, le=2, allow_inf_nan=False)
    max_results: int = Field(default=5, ge=1, le=8)
    travel_mode: Literal["auto", "walking", "transit", "driving"] = "auto"
    max_attractions_per_day: int | None = Field(default=None, ge=1, le=20)
    required_places: str = Field(default="", max_length=2000)
    excluded_places: str = Field(default="", max_length=2000)
    attraction_stay_minutes: int | None = Field(default=None, ge=15, le=480)
    route_order: Literal["original", "nearby"] = "original"
    budget_rules: BudgetRules = Field(default_factory=BudgetRules)
    candidate_limit: int = Field(default=12, ge=1, le=40)
    allocation_days: int | None = Field(default=None, ge=1, le=60)
    allocation_pace: Literal["relaxed", "balanced", "full"] = "balanced"
    allocation_start_time: str = Field(default="09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    allocation_buffer_minutes: int = Field(default=30, ge=0, le=180)
    allocation_default_stay_minutes: int = Field(default=90, ge=15, le=480)


class StudioNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    kind: NodeKind
    title: str = Field(min_length=1, max_length=60, pattern=r"\S")
    x: float = Field(default=0, ge=-5000, le=10000, allow_inf_nan=False)
    y: float = Field(default=0, ge=-5000, le=10000, allow_inf_nan=False)
    parameters: NodeParameters = Field(default_factory=NodeParameters)


class StudioEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    target: str
    source_port: PortType
    target_port: Literal[
        "context", "query", "sources", "content", "itinerary", "budget", "candidates"
    ]


class StudioGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    title: str = Field(default="我的规划工程", min_length=1, max_length=80)
    nodes: list[StudioNode] = Field(default_factory=list, max_length=20)
    edges: list[StudioEdge] = Field(default_factory=list, max_length=80)


class SaveStudio(BaseModel):
    expected_revision: int = Field(ge=0)
    graph: StudioGraph


class StudioSource(BaseModel):
    title: str = Field(max_length=500)
    url: HttpUrl
    snippet: str = Field(max_length=2000)
    provider: str = Field(max_length=100)


class NodeArtifact(BaseModel):
    text: str = Field(default="", max_length=24000)
    sources: list[StudioSource] = Field(default_factory=list, max_length=80)
    plan: StudioPlanData | None = None
    budget: StudioBudgetData | None = None
    candidates: CandidatePool | None = None


class NodeExecution(BaseModel):
    node_id: str
    status: Literal["waiting", "running", "succeeded", "cached", "failed", "blocked"] = "waiting"
    input_key: str = ""
    artifact: NodeArtifact | None = None
    error: str = ""
    error_code: str = ""
    elapsed_ms: int = 0
    produced_at: datetime | None = None


class StudioRunInput(BaseModel):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    expected_revision: int = Field(ge=1)
    target_node: str | None = Field(default=None, max_length=80)
    force: bool = False


class StudioRun(BaseModel):
    id: str
    branch_id: str
    revision: int
    graph: StudioGraph
    target_node: str | None
    force: bool
    status: Literal["running", "succeeded", "failed", "interrupted"] = "running"
    nodes: list[NodeExecution]
    created_at: datetime
    finished_at: datetime | None = None


class StudioProject(BaseModel):
    revision: int = 0
    graph: StudioGraph | None = None
    updated_at: datetime | None = None
    runs: list[StudioRun] = Field(default_factory=list)
    requirements_seed: str = ""


class StudioTemplate(BaseModel):
    id: str
    title: str
    description: str
    graph: StudioGraph


# 输入端口为可多连的集合；资料端口与文本端口不可混接。
INPUT_PORTS: dict[str, dict[str, PortType]] = {
    "input": {},
    "search": {"query": "text"},
    "generate": {"context": "text", "sources": "sources"},
    "plan": {"context": "text", "sources": "sources"},
    "candidates": {"context": "text", "sources": "sources"},
    "allocate": {"candidates": "candidates"},
    "route": {"itinerary": "itinerary"},
    "budget": {"itinerary": "itinerary"},
    "select": {"itinerary": "itinerary"},
    "output": {
        "content": "text",
        "sources": "sources",
        "itinerary": "itinerary",
        "budget": "budget",
    },
}
INPUT_PORTS["generate"].update({"itinerary": "itinerary", "budget": "budget"})
INPUT_PORTS["generate"]["candidates"] = "candidates"
OUTPUT_PORTS: dict[str, dict[str, PortType]] = {
    "input": {"text": "text"},
    "search": {"sources": "sources"},
    "generate": {"text": "text"},
    "plan": {"itinerary": "itinerary"},
    "candidates": {"candidates": "candidates"},
    "allocate": {"itinerary": "itinerary"},
    "route": {"itinerary": "itinerary"},
    "budget": {"budget": "budget"},
    "select": {"itinerary": "itinerary"},
    "output": {},
}
