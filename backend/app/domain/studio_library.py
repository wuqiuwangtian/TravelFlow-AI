"""本机工程模板，不包含来源旅行、运行结果或正式行程。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.workbench import StudioGraph


class UserTemplateSummary(BaseModel):
    id: str
    title: str
    description: str
    revision: int
    node_count: int
    trip_input_count: int
    created_at: datetime
    updated_at: datetime


class UserTemplate(UserTemplateSummary):
    graph: StudioGraph
    trip_input_ids: list[str]


class SaveUserTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    expected_revision: int = Field(default=0, ge=0)
    title: str = Field(min_length=1, max_length=80, pattern=r"\S")
    description: str = Field(default="", max_length=600)
    graph: StudioGraph
    # 省略时默认清空所有文本输入，固定内容必须明确选择保留。
    trip_input_ids: list[str] | None = Field(default=None, max_length=20)


class DeleteUserTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    expected_revision: int = Field(ge=1)


class LoadUserTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
