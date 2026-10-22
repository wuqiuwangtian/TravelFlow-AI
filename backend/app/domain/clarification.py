"""分支中的待澄清操作，不保存已执行一半的账本草稿。"""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.itinerary import Place

ClarificationKind = Literal["day", "amount", "activity"]


class JourneyClarification(BaseModel):
    kind: ClarificationKind
    clauses: list[str] = Field(max_length=12)
    day_scopes: list[int | None] = Field(max_length=12)
    index: int = Field(ge=0, le=11)
    prompt: str
    itinerary_fingerprint: str
    after_message_id: str = ""


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
    pending: JourneyClarification | PlaceClarification | None,
) -> dict[str, Any] | None:
    if not pending:
        return None
    result: dict[str, Any] = {"kind": pending.kind, "prompt": pending.prompt}
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
