"""领域模型 —— 需求、检索结果、引用、待办事项。

对照阶段①缺陷：
- P1-7  旧项目用裸 ``Dict`` 装需求，字段名靠约定，拼错无提示 → 此处强类型化；
- P2-6  旧项目检索 query 硬编码 ``2024`` → 年份改由运行时注入（见 nodes/researcher.py）。
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, Field

from app.domain.enums import DetailLevel, TodoKind
from app.search.base import SearchResult


class Requirements(BaseModel):
    """用户出行需求。四个必填项齐全后方可进入检索阶段。"""

    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = (
        "destination",
        "origin",
        "duration_days",
        "budget",
    )

    destination: str | None = Field(None, description="目的地")
    origin: str | None = Field(None, description="出发地")
    duration_days: int | None = Field(None, ge=1, le=60, description="游玩天数")
    budget: float | None = Field(None, ge=0, description="总预算（元）")
    travelers: int | None = Field(None, ge=1, le=50, description="出行人数")
    preferences: list[str] = Field(default_factory=list, description="偏好标签")

    def missing_fields(self) -> list[str]:
        """返回仍然缺失的必填字段名。"""
        missing: list[str] = []
        for field in self.REQUIRED_FIELDS:
            value = getattr(self, field)
            if value is None or value == "":
                missing.append(field)
        return missing

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields()

    def merged_with(self, other: Requirements) -> Requirements:
        """用 ``other`` 中的非空字段覆盖自身，返回新对象（不改原对象）。"""
        data = self.model_dump()
        for key, value in other.model_dump(exclude_none=True).items():
            if isinstance(value, list) and not value:
                continue
            data[key] = value
        return Requirements(**data)


class Citation(BaseModel):
    """一条引用。``url`` 由程序从检索结果直接透传，**不经大模型**（规划 §19.3）。"""

    index: int
    title: str
    url: str
    provider: str
    retrieved_at: datetime


class ResearchBundle(BaseModel):
    """一次调研的完整产物。"""

    transport: list[SearchResult] = Field(default_factory=list)
    attractions: list[SearchResult] = Field(default_factory=list)
    reviews: list[SearchResult] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    searched_at: datetime = Field(default_factory=datetime.now)

    def merged_results(self, *, per_bucket: int | None = None) -> list[SearchResult]:
        """按「交通 → 景点 → 避雷」顺序合并，供编号。"""
        buckets = (self.transport, self.attractions, self.reviews)
        merged: list[SearchResult] = []
        for bucket in buckets:
            merged.extend(bucket if per_bucket is None else bucket[:per_bucket])
        return merged

    def build_citations(self, *, limit: int = 20) -> list[Citation]:
        """把检索结果编号为引用列表 —— 编号即大模型唯一可见的引用标识。"""
        now = self.searched_at
        return [
            Citation(index=i, title=r.title, url=r.url, provider=r.provider, retrieved_at=now)
            for i, r in enumerate(self.merged_results()[:limit], start=1)
        ]

    def numbered_sources(self, *, limit: int = 20, snippet: int = 500) -> str:
        """渲染给大模型的编号资料块。

        大模型看到的只有「编号 + 标题 + 摘要」，**看不到 URL**，
        因此它没有任何机会改写或编造链接。
        """
        lines: list[str] = []
        for i, result in enumerate(self.merged_results()[:limit], start=1):
            content = (result.content or "").strip().replace("\n", " ")
            lines.append(f"[{i}] {result.title} — {content[:snippet]}")
        return "\n".join(lines) if lines else "（无检索结果）"


class TodoItem(BaseModel):
    """需要用户提前办理或自行核实的事项。

    大模型只负责识别「要办什么」，官方渠道由程序按 ``kind`` 匹配（规划 §19.5）。
    """

    kind: TodoKind = TodoKind.OTHER
    what: str
    when: str | None = None
    note: str | None = None


# ============================================================ 会话 / 分支 / 版本
class SessionInfo(BaseModel):
    """一个旅行会话。"""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    archived: bool = False
    branch_count: int = 0


class BranchInfo(BaseModel):
    """一条规划演化线。``id`` 同时作为 LangGraph 的 ``thread_id``。"""

    id: str
    session_id: str
    title: str
    created_at: datetime
    parent_branch_id: str | None = None
    fork_version_id: str | None = None
    is_primary: bool = False


class VersionInfo(BaseModel):
    """分支上的一个历史版本，对应一个 LangGraph checkpoint。"""

    id: str
    branch_id: str
    seq: int
    summary: str = ""
    detail_level: str = DetailLevel.L2_STANDARD.value
    trigger: str = "user_message"  # user_message / fork / regenerate
    checkpoint_id: str | None = None
    created_at: datetime


class VersionDetail(BaseModel):
    """版本详情：元信息 + 路书内容。"""

    version: VersionInfo
    markdown: str = ""
    citations: list[Citation] = Field(default_factory=list)
    todos: list[TodoItem] = Field(default_factory=list)
    searched_at: datetime | None = None
