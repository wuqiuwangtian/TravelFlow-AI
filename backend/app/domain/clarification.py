"""分支中的待澄清操作，不保存已执行一半的账本草稿。"""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.itinerary import Place
from app.domain.linked_replan import LinkedReplanPreview, MoveClarification

ClarificationKind = Literal["day", "amount", "activity", "expense"]


class ExpenseTarget(BaseModel):
    """账本位置与内容同时绑定；本轮草稿中的新消费尚无持久 ID。"""

    index: int = Field(ge=0, le=1999)
    expense_id: str | None = None
    fingerprint: str
    label: str


class JourneyClarification(BaseModel):
    kind: ClarificationKind
    clauses: list[str] = Field(max_length=12)
    day_scopes: list[int | None] = Field(max_length=12)
    index: int = Field(ge=0, le=11)
    prompt: str
    itinerary_fingerprint: str
    after_message_id: str = ""
    expense_options: list[ExpenseTarget] = Field(default_factory=list, max_length=2000)
    expense_selections: dict[int, ExpenseTarget] = Field(default_factory=dict)
    expense_page: int = Field(default=0, ge=0)


class PlaceTarget(BaseModel):
    day: int
    ordinal: int
    activity_id: str
    title: str


class PlaceClarification(BaseModel):
    kind: Literal["place"] = "place"
    day: int | None = None
    activity_id: str | None = None
    targets: list[PlaceTarget] = Field(default_factory=list, max_length=12)
    candidates: list[Place] = Field(default_factory=list, max_length=8)
    query_name: str = ""
    city: str = ""
    replacement: bool = False
    prompt: str = ""
    itinerary_fingerprint: str
    after_message_id: str = ""


def clarification_prompt(
    pending: JourneyClarification
    | PlaceClarification
    | LinkedReplanPreview
    | MoveClarification
    | None,
) -> dict[str, Any] | None:
    if not pending:
        return None
    result: dict[str, Any] = {"kind": pending.kind, "prompt": pending.prompt}
    if isinstance(pending, MoveClarification):
        result["options"] = [
            {"label": f"{i}. {c.label}", "reply": f"选第{i}个"}
            for i, c in enumerate(pending.choices, 1)
        ]
    if isinstance(pending, LinkedReplanPreview):
        result["changes"] = [c.model_dump() for c in pending.changes]
        result["conflicts"] = pending.conflicts
        result["warnings"] = pending.warnings
        result["options"] = (
            [] if pending.conflicts else [{"label": "应用两天调整", "reply": "应用两天调整"}]
        )
    if isinstance(pending, JourneyClarification) and pending.kind == "expense":
        start = pending.expense_page * 8
        end = min(start + 8, len(pending.expense_options))
        result["prompt"] = pending.prompt
        result["options"] = [
            {"label": f"{i + 1}. {t.label}", "reply": f"选第{i + 1}笔"}
            for i, t in enumerate(pending.expense_options[start:end], start)
        ]
        if start:
            result["options"].append({"label": "上一页消费", "reply": "上一页消费"})
        if end < len(pending.expense_options):
            result["options"].append({"label": "下一页消费", "reply": "下一页消费"})
    if isinstance(pending, PlaceClarification):
        result["prompt"] = "\n".join(
            line
            for line in pending.prompt.splitlines()
            if not re.match(r"^(?:\d+\. |第\d+天第\d+站 · )", line)
        )
        result["options"] = (
            [
                {
                    "label": f"{i}. {p.name} · {p.city}{p.district} {p.address}",
                    "reply": f"选第{i}个",
                }
                for i, p in enumerate(pending.candidates, 1)
            ]
            if pending.activity_id
            else [
                {
                    "label": f"第{t.day}天第{t.ordinal}站 · {t.title}",
                    "reply": f"第{t.day}天第{t.ordinal}站",
                }
                for t in pending.targets
            ]
        )
    return result
